import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication

from napback import core
from napback.tray import TrayController, state_label, update_interval


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def configured(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_text("content")
    data = core.initialize(tmp_path / "repository")
    data.update(sources=[{"name": "files", "path": str(source)}], min_free_bytes=0)
    path = tmp_path / "config.json"
    core.write_json(path, data)
    return path


def test_unconfigured_tray_is_visible(application, tmp_path):
    tray = TrayController(tmp_path / "not-configured.json", start_timer=False)
    assert tray.tray.isVisible()
    assert tray.state == "unconfigured"
    assert not tray.check_action.isEnabled()
    assert not tray.tray.icon().isNull()
    tray.tray.hide()


def test_tray_tracks_success_failure_and_running_state(application, configured):
    config = core.Config.load(configured)
    tray = TrayController(configured, start_timer=False)
    try:
        core.run(config)
        tray.refresh()
        assert tray.state == "completed"
        with core.locked(config), core.checking(config, 100001):
            tray.refresh()
            assert tray.state == "checking"
            assert not tray.check_action.isEnabled()
        core.write_json(
            config.target / "last-check.json", {"status": "failed", "error": "network down"}
        )
        tray.refresh()
        assert tray.state == "failed" and "network down" in tray.tray.toolTip()
    finally:
        tray.tray.hide()


def test_interval_editor_preserves_other_settings_and_backs_up(configured):
    before = core.read_json(configured)
    update_interval(configured, 7)
    after = core.read_json(configured)
    assert after == {**before, "check_interval_minutes": 7}
    backups = list(configured.parent.glob("config.json.bak-*"))
    assert len(backups) == 1 and core.read_json(backups[0]) == before


def test_manual_check_uses_independent_service(application, configured):
    tray = TrayController(configured, start_timer=False)
    try:
        with patch("napback.tray.QProcess") as process:
            tray.check_now()
            command, args = process.return_value.start.call_args.args
            assert command == "systemd-run"
            assert "--user" in args and "run" in args
            assert "--force" not in args
            assert any(arg.startswith("--unit=napback-manual-") for arg in args)
    finally:
        tray.tray.hide()


def test_german_labels():
    assert state_label("running", True) == "Sicherung läuft"
    assert "kein neuer Snapshot" in state_label("no_new_snapshot", True)


def test_tray_setup_opens_browser_without_terminal(application, tmp_path):
    path = tmp_path / "config.json"
    tray = TrayController(path, start_timer=False)
    try:
        with patch("napback.tray.QProcess.startDetached") as start:
            tray.setup()
        assert start.call_args.args[1] == ["-m", "napback", "--config", str(path), "ui"]
    finally:
        tray.tray.hide()
