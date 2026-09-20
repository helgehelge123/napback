"""Command-line interface and systemd user integration."""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, core, integrity


def default_config():
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "napback/config.json"
    )


def backup_existing(path):
    if path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(path.name + ".bak-" + timestamp)
        shutil.copy2(path, backup)
        print(f"Backup: {backup}", file=sys.stderr)


def unit_quote(value):
    return (
        '"'
        + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        + '"'
    )


def unit_contents(config_path):
    executable = Path(sys.executable).absolute()
    command = f"{unit_quote(executable)} -m napback --config {unit_quote(config_path.absolute())} run --scheduled"
    service = f"""[Unit]
Description=Napback: check for new snapshots and pull changed generations

[Service]
Type=oneshot
ExecStart={command}
TimeoutStartSec=infinity
TimeoutStopSec=20
KillMode=control-group
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
UMask=0077
NoNewPrivileges=true
"""
    timer = """[Unit]
Description=Check whether a Napback backup is due after boot, resume and each minute

[Timer]
OnStartupSec=30s
OnCalendar=*-*-* *:*:00
Persistent=true
AccuracySec=10s
Unit=napback.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def install_timer(config_path):
    core.Config.load(config_path)
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
    root.mkdir(parents=True, exist_ok=True)
    for suffix, contents in zip(("service", "timer"), unit_contents(config_path)):
        path = root / f"napback.{suffix}"
        backup_existing(path)
        path.write_text(contents)
    core.command(["systemctl", "--user", "daemon-reload"])
    core.command(["systemctl", "--user", "enable", "--now", "napback.timer"])
    return {
        "status": "timer_enabled",
        "units": str(root),
        "note": "Runs at login; enable user lingering with loginctl enable-linger for execution before login.",
    }


def setup(config_path):
    """Interactive setup keeps credentials in SSH, never in application config."""
    print("Napback — pull ZFS snapshots when this PC is awake.")
    host = input("SSH host (existing alias or user@nas): ").strip()
    use_sudo = input("Use sudo -n on the NAS? [Y/n]: ").strip().lower() != "n"
    probe = core.Config(Path("/"), str(uuid.uuid4()), Path("/"), [], host=host, sudo=use_sudo)
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
        raise core.BackupError("Invalid SSH host")
    print(probe.remote(["zfs", "list", "-H", "-o", "name,used,mountpoint", "-t", "filesystem"]))
    names = input("Datasets to back up, separated by commas (children included): ").split(",")
    sources = [
        {"name": f"dataset-{i + 1}", "dataset": name.strip(), "recursive": True}
        for i, name in enumerate(names)
        if name.strip()
    ]
    target = input("New or empty destination directory on this PC: ").strip()
    image = input("NAS Docker image [empty = installed rsync; e.g. napback-source:0.1.0]: ").strip()
    config = {
        "target": target,
        "repository_id": str(uuid.uuid4()),
        "mountpoint": "/",
        "host": host,
        "sudo": use_sudo,
        "sources": sources,
    }
    interval = input("Check for new snapshots every how many minutes? [1]: ").strip()
    config["check_interval_minutes"] = int(interval or "1")
    config["trigger"] = "new_snapshot"
    print(
        "Backups contain readable files. ZFS encryption is not inherited; use an encrypted destination filesystem if needed."
    )
    if image:
        config["docker_image"] = image
    # Validate and plan before creating the repository or replacing existing config.
    with tempfile.TemporaryDirectory() as temporary:
        temporary_config = Path(temporary) / "config.json"
        core.write_json(temporary_config, config)
        checked = core.Config.load(temporary_config)
        core.plan_sources(checked, time.time())
    config.update(core.initialize(target))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_existing(config_path)
    core.write_json(config_path, config)
    print(f"Configuration: {config_path}")
    if input("Enable the background timer? [Y/n]: ").strip().lower() != "n":
        install_timer(config_path)
    return {"status": "configured", "target": config["target"]}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pull versioned NAS backups when your Linux PC is awake"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=default_config())
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("setup", help="Interactive SSH/ZFS setup")
    commands.add_parser("tray", help="Show background status in the desktop system tray")
    commands.add_parser(
        "install-tray", help="Install and start the desktop tray with login autostart"
    )
    init = commands.add_parser("init", help="Initialize a new or empty backup directory")
    init.add_argument("target", type=Path)
    run = commands.add_parser("run", help="Back up only if due")
    run.add_argument("--force", action="store_true")
    run.add_argument(
        "--scheduled", action="store_true", help="Respect the configured polling interval"
    )
    commands.add_parser("status", help="Show last success, due state and incomplete attempts")
    commands.add_parser("plan", help="List selected sources without copying")
    commands.add_parser("list", help="List completed snapshots")
    commands.add_parser("install-timer", help="Install and enable the systemd user timer")
    verify = commands.add_parser(
        "verify", help="Verify local contents and xattrs against the saved SHA-256 inventory"
    )
    verify.add_argument("snapshot", nargs="?", default="latest")
    restore = commands.add_parser(
        "restore", help="Verify and copy a backup into a new or empty directory"
    )
    restore.add_argument("destination", type=Path)
    restore.add_argument("--snapshot", default="latest")
    args = parser.parse_args(argv)

    def interrupted(signum, frame):
        raise core.BackupError(
            f"Interrupted by signal {signum}; no incomplete backup will be published"
        )

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.action == "tray":
            from .tray import main as tray_main

            return tray_main(args.config)
        if args.action == "install-tray":
            from .desktop import install_tray

            result = install_tray(args.config)
        elif args.action == "init":
            result = core.initialize(args.target)
        elif args.action == "setup":
            result = setup(args.config)
        elif args.action == "install-timer":
            result = install_timer(args.config)
        else:
            config = core.Config.load(args.config)
            if args.action == "run":
                result = core.run(config, force=args.force, scheduled=args.scheduled)
            elif args.action == "status":
                result = core.status(config)
            elif args.action == "plan":
                result = [core.asdict(source) for source in core.plan_sources(config, time.time())]
            elif args.action == "list":
                with core.locked(config):
                    result = [manifest for _, manifest in core.completed(config)]
            else:
                with core.locked(config):
                    entries = core.completed(config)
                    if not entries:
                        raise core.BackupError("No completed backups")
                    selected = (
                        entries[-1]
                        if args.snapshot == "latest"
                        else next((item for item in entries if item[0].name == args.snapshot), None)
                    )
                    if selected is None:
                        raise core.BackupError("Snapshot not found")
                    path, manifest = selected
                    count = integrity.verify(path, manifest)
                    result = {"status": "verified", "id": path.name, "entries": count}
                    if args.action == "restore":
                        destination = core.absolute(str(args.destination), "restore destination")
                        core.no_symlink(destination)
                        if destination == config.target or config.target in destination.parents:
                            raise core.BackupError(
                                "Restore destination must be outside the repository"
                            )
                        if destination.exists() and any(destination.iterdir()):
                            raise core.BackupError("Restore destination must be new or empty")
                        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                        # Unprivileged file restore: preserve contents, links, times, user xattrs.
                        # Keep fake-super metadata in the backup; do not export it as real ownership.
                        restore_argv = [
                            "rsync",
                            "-rltHX",
                            "--fake-super",
                            "-e",
                            shlex.join([sys.executable, "-m", "napback.local_transport"]),
                            "--protect-args",
                            "--info=NONREG0",
                            "--no-specials",
                            "--no-devices",
                            "--",
                            str(path / "data") + "/",
                            "napback-local:" + str(destination) + "/",
                        ]
                        core.command(restore_argv, timeout=config.timeout_seconds)
                        changes = core.command(
                            [
                                restore_argv[0],
                                "--dry-run",
                                "--checksum",
                                "--delete",
                                "--itemize-changes",
                                *restore_argv[1:],
                            ],
                            timeout=config.timeout_seconds,
                        )
                        if changes.strip():
                            raise core.BackupError(
                                "Restored files failed verification: " + changes[:1500]
                            )
                        result.update(
                            status="restored",
                            destination=str(destination),
                            note="File restore; use documented root rsync procedure for original ownership, ACLs and special files.",
                        )
        print(json.dumps(result, indent=2))
        return 0
    except (
        core.BackupError,
        ImportError,
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
        KeyboardInterrupt,
    ) as error:
        print(f"napback: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
