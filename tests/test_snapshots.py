from unittest.mock import patch

import pytest

from napback import core


@pytest.fixture
def snapshot_job(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("snapshot one")
    settings = core.initialize(tmp_path / "repository")
    settings.update(
        host="nas", sources=[{"name": "files", "dataset": "tank/files"}], min_free_bytes=0
    )
    config_path = tmp_path / "config.json"
    core.write_json(config_path, settings)
    config = core.Config.load(config_path)
    config.host = None  # Real local rsync with mocked ZFS discovery, no SSH.
    selected = core.Source("files", str(source), "tank/files", "auto-one", 100000, "123")
    return source, config, config_path, selected


def run_selected(config, selected, **kwargs):
    with (
        patch("napback.core.plan_sources", return_value=[selected]),
        patch.object(core.Config, "remote", return_value=""),
    ):
        return core.run(config, **kwargs)


def test_same_snapshot_is_not_copied_even_after_24_hours(snapshot_job):
    _, config, _, selected = snapshot_job
    assert run_selected(config, selected, now=100000)["status"] == "completed"
    assert run_selected(config, selected, now=200000)["status"] == "no_new_snapshot"
    assert len(core.completed(config)) == 1
    assert core.status(config)["last_success"] == 100000
    assert core.status(config)["last_check"]["status"] == "no_new_snapshot"
    assert core.status(config)["due"] is None


def test_new_guid_is_copied_immediately_even_with_same_name(snapshot_job):
    source, config, _, selected = snapshot_job
    run_selected(config, selected, now=100000)
    selected.guid = "456"
    (source / "file").write_text("snapshot two")
    assert run_selected(config, selected, now=100001)["status"] == "completed"
    entries = core.completed(config)
    assert (entries[0][0] / "data/files/file").read_text() == "snapshot one"
    assert (entries[1][0] / "data/files/file").read_text() == "snapshot two"


def test_failed_new_snapshot_is_retried(snapshot_job):
    _, config, _, selected = snapshot_job
    run_selected(config, selected, now=100000)
    selected.guid = "456"
    with patch("napback.core.command", side_effect=core.BackupError("offline")):
        with pytest.raises(core.BackupError, match="offline"):
            run_selected(config, selected, now=100001)
    assert len(core.completed(config)) == 1
    assert core.status(config)["last_check"]["status"] == "failed"
    assert run_selected(config, selected, now=100002)["status"] == "completed"


def test_scheduled_poll_interval_is_dynamic_and_manual_check_bypasses_it(snapshot_job):
    _, config, _, selected = snapshot_job
    config.check_interval_minutes = 10
    run_selected(config, selected, now=100000, scheduled=True)
    with patch("napback.core.plan_sources") as plan:
        assert core.run(config, now=100599, scheduled=True)["status"] == "check_not_due"
        plan.assert_not_called()
    assert run_selected(config, selected, now=100600, scheduled=True)["status"] == "no_new_snapshot"
    assert run_selected(config, selected, now=100601)["status"] == "no_new_snapshot"
    config.check_interval_minutes = 1
    assert run_selected(config, selected, now=100661, scheduled=True)["status"] == "no_new_snapshot"
    assert len(core.completed(config)) == 1


def test_force_makes_explicit_duplicate(snapshot_job):
    _, config, _, selected = snapshot_job
    run_selected(config, selected, now=100000)
    assert run_selected(config, selected, force=True, now=100001)["status"] == "completed"
    assert len(core.completed(config)) == 2


def test_status_is_readable_while_worker_holds_lock(snapshot_job):
    _, config, _, _ = snapshot_job
    with core.locked(config):
        with core.checking(config, 100000):
            status = core.status(config)
            assert status["running"] is True
            assert status["last_check"]["status"] == "checking"


def test_snapshot_discovery_error_is_visible_without_changing_last_success(snapshot_job):
    _, config, _, selected = snapshot_job
    run_selected(config, selected, now=100000)
    with patch("napback.core.plan_sources", side_effect=core.BackupError("NAS unavailable")):
        with pytest.raises(core.BackupError):
            core.run(config, now=100001)
    status = core.status(config)
    assert status["last_success"] == 100000
    assert status["last_check"]["error"] == "NAS unavailable"


def test_legacy_manifest_without_guid_gets_one_refreshed_backup(snapshot_job):
    _, config, _, selected = snapshot_job
    run_selected(config, selected, now=100000)
    path, manifest = core.completed(config)[0]
    manifest["sources"][0].pop("guid")
    core.write_json(path / "manifest.json", manifest)
    assert run_selected(config, selected, now=100001)["status"] == "completed"
    assert run_selected(config, selected, now=100002)["status"] == "no_new_snapshot"


@pytest.mark.parametrize("value", [0, 1441, 1.5, True, "5"])
def test_invalid_poll_interval(snapshot_job, value):
    _, _, filename, _ = snapshot_job
    data = core.read_json(filename)
    data["check_interval_minutes"] = value
    core.write_json(filename, data)
    with pytest.raises(core.BackupError, match="check_interval_minutes"):
        core.Config.load(filename)


def test_explicit_snapshot_mode_refuses_live_directory(tmp_path):
    data = core.initialize(tmp_path / "backup")
    data.update(sources=[{"name": "files", "path": "/tmp/source"}], trigger="new_snapshot")
    path = tmp_path / "config.json"
    core.write_json(path, data)
    with pytest.raises(core.BackupError, match="requires ZFS"):
        core.Config.load(path)


def test_poll_configuration_preserves_content_fingerprint(snapshot_job):
    _, config, _, _ = snapshot_job
    original = config.fingerprint()
    config.check_interval_minutes = 99
    config.trigger = "interval"
    assert config.fingerprint() == original


@pytest.mark.parametrize("state", ["failed", "checking", "running", "interrupted"])
def test_unsuccessful_scheduled_checks_retry_after_one_minute(snapshot_job, state):
    _, config, _, selected = snapshot_job
    config.check_interval_minutes = 60
    run_selected(config, selected, now=100000, scheduled=True)
    path = config.target / "last-check.json"
    previous = core.read_json(path)
    previous.update(status=state, error="NAS unavailable")
    core.write_json(path, previous)
    with patch("napback.core.plan_sources") as plan:
        assert core.run(config, now=100059, scheduled=True)["status"] == "check_not_due"
        plan.assert_not_called()
    assert run_selected(config, selected, now=100060, scheduled=True)["status"] == "no_new_snapshot"
    assert "error" not in core.status(config)["last_check"]
    assert config.check_interval_minutes == 60
    # Once the connection succeeds, the user's ordinary interval applies again.
    with patch("napback.core.plan_sources") as plan:
        assert core.run(config, now=100120, scheduled=True)["status"] == "check_not_due"
        plan.assert_not_called()
    assert len(core.completed(config)) == 1


def test_real_discovery_failure_gets_early_scheduled_retry(snapshot_job):
    _, config, _, selected = snapshot_job
    config.check_interval_minutes = 1440
    with patch("napback.core.plan_sources", side_effect=core.BackupError("Network is unreachable")):
        with pytest.raises(core.BackupError, match="Network is unreachable"):
            core.run(config, now=100000, scheduled=True)
    assert core.status(config)["last_check"]["status"] == "failed"
    assert not core.completed(config)
    assert run_selected(config, selected, now=100060, scheduled=True)["status"] == "completed"
    assert config.check_interval_minutes == 1440
