"""Desktop installation without touching backup selection or enabling a backup job."""

import os
import sys
from pathlib import Path

from . import core
from .cli import backup_existing, unit_quote


def install_tray(config_path):
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    data_root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    executable = Path(sys.executable).absolute()
    command = (
        f"{unit_quote(executable)} -m napback --config {unit_quote(config_path.absolute())} tray"
    )
    unit = f"""[Unit]
Description=Napback system tray
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=10s
UMask=0077
"""
    desktop = """[Desktop Entry]
Type=Application
Name=Napback
Comment=Watch NAS snapshot backups
Exec=systemctl --user start napback-tray.service
Icon=drive-harddisk
Terminal=false
Categories=Utility;Archiving;
StartupNotify=false
"""
    for path, text in (
        (config_root / "systemd/user/napback-tray.service", unit),
        (config_root / "autostart/napback.desktop", desktop),
        (data_root / "applications/napback.desktop", desktop),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_existing(path)
        path.write_text(text)
    graphical_session = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    try:
        core.command(["systemctl", "--user", "daemon-reload"])
    except core.BackupError:
        if graphical_session:
            raise
        # Offline/headless installation: the next desktop login starts the tray.
    if graphical_session:
        environment = [
            name
            for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE")
            if name in os.environ
        ]
        core.command(["systemctl", "--user", "import-environment", *environment])
        # Installation/update may restart the tray, never the backup worker.
        core.command(["systemctl", "--user", "restart", "napback-tray.service"])
        started = True
    else:
        started = False
    return {
        "status": "tray_installed",
        "started": started,
        "autostart": str(config_root / "autostart/napback.desktop"),
    }
