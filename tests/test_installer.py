import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from napback.desktop import install_tray


@pytest.mark.parametrize(
    "manager,ssh_package,extra",
    [
        ("pacman", "openssh", "--needed"),
        ("apt-get", "openssh-client", "install -y"),
        ("dnf", "openssh-clients", "install -y"),
    ],
)
def test_missing_packages_are_installed_with_native_manager(tmp_path, manager, ssh_package, extra):
    # A private PATH contains fake executables only: no actual package manager runs.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    for name, content in {
        "id": "#!/bin/sh\necho 0\n",
        manager: '#!/bin/sh\nprintf "%s\\n" "$*" >> "$NAPBACK_TEST_CALLS"\n',
    }.items():
        path = bin_dir / name
        path.write_text(content)
        path.chmod(0o755)
    helper = Path(__file__).resolve().parents[1] / "scripts/system-deps.sh"
    script = f'. "{helper}"\nnapback_with_tray=yes\ninstall_system_dependencies\n'
    result = subprocess.run(
        ["/bin/sh", "-c", script],
        env={"PATH": str(bin_dir), "NAPBACK_TEST_CALLS": str(log)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert ssh_package in calls and "rsync" in calls and extra in calls
    assert "systemd" in calls
    if manager == "apt-get":
        assert "python3-venv" in calls and "libxcb-cursor0" in calls


def test_desktop_install_is_separate_from_backup_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("DISPLAY", ":99")
    with patch("napback.desktop.core.command", return_value="") as command:
        result = install_tray(tmp_path / "backup.json")
    assert result["started"] is True
    calls = [call.args[0] for call in command.call_args_list]
    assert calls[-1][-1] == "napback-tray.service"
    assert not any("napback.timer" in call for call in calls)
    desktop = tmp_path / "config/autostart/napback.desktop"
    assert desktop.exists() and "napback-tray.service" in desktop.read_text()


def test_headless_install_prepares_autostart_without_a_user_bus(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from napback import core

    with patch("napback.desktop.core.command", side_effect=core.BackupError("No user bus")):
        result = install_tray(tmp_path / "backup.json")
    assert result["status"] == "tray_installed" and result["started"] is False
    assert (tmp_path / "config/autostart/napback.desktop").exists()
