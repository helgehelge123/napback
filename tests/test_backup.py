import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from napback import core, integrity
from napback.cli import main, unit_contents


@pytest.fixture
def job(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "unchanged.txt").write_text("unchanged")
    (source / "changed.txt").write_text("version 1")
    (source / "deleted.txt").write_text("keep in old snapshot")
    repository = tmp_path / "backups"
    settings = core.initialize(repository)
    settings.update(sources=[{"name": "files", "path": str(source)}], min_free_bytes=0, keep=3)
    filename = tmp_path / "config.json"
    core.write_json(filename, settings)
    return source, core.Config.load(filename), filename


def snapshots(config):
    return core.completed(config)


def test_versioning_dedup_metadata_and_restore(job, tmp_path):
    source, config, filename = job
    (source / "sub directory").mkdir()
    (source / "sub directory" / "Ünicode\nfile").write_text("hello")
    (source / "symlink").symlink_to("changed.txt")
    os.link(source / "unchanged.txt", source / "hardlink")
    os.setxattr(source / "unchanged.txt", "user.test", b"value")
    first = core.run(config, now=100000)
    assert first["verified"] is True
    old, old_manifest = snapshots(config)[0]
    original_stat = (source / "changed.txt").stat()
    (source / "changed.txt").write_text("version 2")
    os.utime(source / "changed.txt", ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    (source / "deleted.txt").unlink()
    (source / "new.txt").write_text("new file")
    core.run(config, now=100000 + 86400)
    new, manifest = snapshots(config)[-1]
    old_data, new_data = old / "data/files", new / "data/files"
    assert (old_data / "changed.txt").read_text() == "version 1"
    assert (new_data / "changed.txt").read_text() == "version 2"
    assert (old_data / "deleted.txt").exists()
    assert not (new_data / "deleted.txt").exists()
    assert (new_data / "symlink").is_symlink() or os.getxattr(
        new_data / "symlink", "user.rsync.%stat"
    ).startswith(b"120777")
    assert (old_data / "unchanged.txt").stat().st_ino == (new_data / "unchanged.txt").stat().st_ino
    assert (new_data / "hardlink").stat().st_ino == (new_data / "unchanged.txt").stat().st_ino
    assert os.getxattr(new_data / "unchanged.txt", "user.test") == b"value"
    assert integrity.verify(old, old_manifest) > 0
    assert integrity.verify(new, manifest) > 0
    restored = tmp_path / "restore"
    assert main(["--config", str(filename), "restore", str(restored)]) == 0
    assert (restored / "files/changed.txt").read_text() == "version 2"
    assert (restored / "files/symlink").is_symlink()
    assert os.readlink(restored / "files/symlink") == "changed.txt"
    assert (restored / "files/sub directory/Ünicode\nfile").read_text() == "hello"


def test_exact_due_interval_failure_and_retry(job):
    source, config, _ = job
    core.run(config, now=100000)
    assert core.run(config, now=100000 + 86399)["status"] == "not_due"
    with patch("napback.core.command", side_effect=core.BackupError("network down")):
        with pytest.raises(core.BackupError, match="network down"):
            core.run(config, now=186400)
    assert snapshots(config)[-1][1]["completed_at"] == 100000
    assert core.status(config)["incomplete"] == 1
    assert core.run(config, now=186401)["status"] == "completed"
    assert core.status(config)["incomplete"] == 0


def test_future_clock_and_config_change_force_due(job):
    _, config, _ = job
    core.run(config, now=100000)
    assert not core.due(config, snapshots(config), 99900)
    assert core.due(config, snapshots(config), 99000)
    config.verify = False
    assert core.due(config, snapshots(config), 100001)


def test_failure_of_second_source_publishes_nothing(job, tmp_path):
    _, config, _ = job
    core.run(config, now=100000)
    config.sources.append({"name": "missing", "path": str(tmp_path / "missing")})
    with pytest.raises(FileNotFoundError):
        core.run(config, force=True)
    assert len(snapshots(config)) == 1


def test_failed_verification_does_not_publish(job):
    _, config, _ = job
    original = core.command

    def inconsistent(argv, **kwargs):
        if "--dry-run" in argv:
            return ">fcs....... changed.txt\n"
        return original(argv, **kwargs)

    with patch("napback.core.command", side_effect=inconsistent):
        with pytest.raises(core.BackupError, match="Verification failed"):
            core.run(config)
    assert snapshots(config) == []


def test_retention_only_after_success(job):
    _, config, _ = job
    for i in range(5):
        core.run(config, force=True, now=100000 + i)
    assert len(snapshots(config)) == 3
    with patch("napback.core.command", side_effect=core.BackupError("disk full")):
        with pytest.raises(core.BackupError):
            core.run(config, force=True)
    assert len(snapshots(config)) == 3
    config.keep = 0
    core.run(config, force=True)
    assert len(snapshots(config)) == 4


def test_lock_prevents_parallel_run(job):
    _, config, _ = job
    with core.locked(config):
        with pytest.raises(core.BackupError, match="Another backup"):
            core.run(config)


def test_missing_mount_and_repository_identity(job):
    _, config, _ = job
    with patch.object(Path, "is_mount", return_value=False):
        with pytest.raises(core.BackupError, match="not mounted"):
            core.run(config)
    config.repository_id = "00000000-0000-4000-8000-000000000001"
    with pytest.raises(core.BackupError, match="identity mismatch"):
        core.run(config)


def test_missing_repository_never_created(job):
    _, config, _ = job
    config.target = config.target / "missing"
    with pytest.raises(core.BackupError):
        core.run(config)
    assert not config.target.exists()


def test_low_space_does_not_delete_snapshots(job):
    _, config, _ = job
    core.run(config)
    config.min_free_bytes = 10**18
    with pytest.raises(core.BackupError, match="free space"):
        core.run(config, force=True)
    assert len(snapshots(config)) == 1


def test_symlink_repository_rejected(job, tmp_path):
    _, config, _ = job
    link = tmp_path / "redirect"
    link.symlink_to(config.target, target_is_directory=True)
    config.target = link
    with pytest.raises(core.BackupError, match="Symlink"):
        core.run(config)


def test_unknown_work_and_snapshot_entries_fail_closed(job):
    _, config, _ = job
    (config.target / "work/precious.txt").write_text("unrelated")
    with pytest.raises(core.BackupError, match="Unexpected work"):
        core.run(config)
    assert (config.target / "work/precious.txt").read_text() == "unrelated"
    (config.target / "snapshots/foreign").mkdir()
    with pytest.raises(core.BackupError, match="Unexpected entry"):
        core.run(config)


def test_corruption_detected_offline(job):
    _, config, _ = job
    core.run(config)
    path, manifest = snapshots(config)[-1]
    (path / "data/files/changed.txt").write_text("corrupted")
    with pytest.raises(core.BackupError, match="Integrity mismatch"):
        integrity.verify(path, manifest)


def test_target_source_overlap_rejected(job):
    _, config, _ = job
    config.sources[0]["path"] = str(config.target.parent)
    with pytest.raises(core.BackupError, match="overlap"):
        core.run(config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("keep", -1),
        ("keep", True),
        ("verify", "yes"),
        ("interval_hours", float("nan")),
        ("host", "-oProxyCommand=evil"),
        ("extra", 1),
        ("sources", [{"name": "../escape", "path": "/tmp"}]),
        ("sources", [{"name": "test", "path": "/tmp", "dataset": "tank/test"}]),
        ("sources", [{"name": "test", "path": "/tmp/../data"}]),
    ],
)
def test_invalid_config(job, key, value):
    _, _, filename = job
    data = json.loads(filename.read_text())
    data[key] = value
    filename.write_text(json.dumps(data))
    with pytest.raises(core.BackupError):
        core.Config.load(filename)


def test_init_refuses_existing_data(tmp_path):
    (tmp_path / "important").write_text("preserve")
    with pytest.raises(core.BackupError, match="new or empty"):
        core.initialize(tmp_path)
    assert (tmp_path / "important").read_text() == "preserve"


def test_common_snapshot_selection_freshness_and_missing_child():
    rows = [
        ("tank/root@auto-old", 100),
        ("tank/root/child@auto-old", 100),
        ("tank/root@auto-new", 200),
        ("tank/root@manual", 300),
    ]
    tag, _ = core.choose_common_snapshot(["tank/root", "tank/root/child"], rows, "auto-", 200, 150)
    assert tag == "auto-old"
    with pytest.raises(core.BackupError, match="too old"):
        core.choose_common_snapshot(["tank/root", "tank/root/child"], rows, "auto-", 1000, 150)
    with pytest.raises(core.BackupError, match="No common"):
        core.choose_common_snapshot(["tank/root", "tank/root/missing"], rows, "auto-", 200, 150)
    with pytest.raises(core.BackupError, match="future"):
        core.choose_common_snapshot(
            ["tank/root"], [("tank/root@auto-future", 1000)], "auto-", 200, 150
        )


def test_docker_is_read_only_and_ssh_host_keys_are_checked(job):
    _, config, _ = job
    config.host = "backup@nas"
    config.sudo = True
    config.docker_image = "napback-source:0.1.0"
    argv = core.transfer_command(
        config, core.Source("files", "/mnt/tank/.zfs/snapshot/auto-1"), Path("/tmp/destination")
    )
    remote = next(arg for arg in argv if arg.startswith("--rsync-path="))
    assert "--network=none" in remote and "--read-only" in remote
    assert "readonly" in remote and "--cap-drop=ALL" in remote
    assert "--privileged" not in remote
    assert "StrictHostKeyChecking=yes" in argv[argv.index("-e") + 1]
    assert "backup@nas:/source/" in argv


def test_timeout_kills_child_process_group(tmp_path):
    marker = tmp_path / "escaped"
    script = 'import subprocess,time,sys; subprocess.Popen([sys.executable,"-c",sys.argv[1]]); time.sleep(10)'
    child = f"import time,pathlib; time.sleep(1); pathlib.Path({str(marker)!r}).touch()"
    with pytest.raises(subprocess.TimeoutExpired):
        core.command([sys.executable, "-c", script, child], timeout=0.1)
    time.sleep(1.1)
    assert not marker.exists()


def test_timer_covers_start_resume_and_user_paths():
    service, timer = unit_contents(Path("/tmp/path with spaces/50%/$config.json"))
    assert "50%%/$$config.json" in service
    assert "OnCalendar=*-*-* *:*:00" in timer
    assert "OnStartupSec=30s" in timer
    assert "Persistent=true" in timer


def test_restore_rejects_nonempty_destination(job, tmp_path):
    _, config, filename = job
    core.run(config)
    restore = tmp_path / "existing"
    restore.mkdir()
    (restore / "precious").write_text("original")
    assert main(["--config", str(filename), "restore", str(restore)]) == 1
    assert (restore / "precious").read_text() == "original"


def test_metadata_change_does_not_mutate_older_hardlinks(job):
    source, config, _ = job
    core.run(config, now=100000)
    old, manifest = snapshots(config)[0]
    os.setxattr(source / "unchanged.txt", "user.changed", b"new")
    os.chmod(source / "unchanged.txt", 0o444)
    core.run(config, force=True, now=100001)
    assert integrity.verify(old, manifest) > 0
    new = snapshots(config)[-1][0]
    assert (old / "data/files/unchanged.txt").stat().st_ino != (
        new / "data/files/unchanged.txt"
    ).stat().st_ino


def test_crash_after_publish_recovers_latest_and_success(job):
    _, config, _ = job
    with patch("napback.core.update_latest", side_effect=[None, core.BackupError("crash")]):
        with pytest.raises(core.BackupError, match="crash"):
            core.run(config, now=100000)
    assert len(snapshots(config)) == 1
    assert not (config.target / "latest").exists()
    assert core.run(config, now=100001)["status"] == "not_due"
    assert (config.target / "latest/files/changed.txt").read_text() == "version 1"


def test_crash_during_retention_does_not_damage_completed_entries(job):
    _, config, _ = job
    config.keep = 1
    core.run(config, now=100000)
    with patch("napback.core.shutil.rmtree", side_effect=core.BackupError("crash deleting")):
        with pytest.raises(core.BackupError, match="crash deleting"):
            core.run(config, force=True, now=100001)
    assert len(snapshots(config)) == 1
    assert core.status(config)["incomplete"] == 1
    core.run(config, force=True, now=100002)
    assert len(snapshots(config)) == 1
    assert core.status(config)["incomplete"] == 0


def test_inventory_detects_extra_missing_xattr_and_symlink_changes(job):
    source, config, _ = job
    (source / "link").symlink_to("changed.txt")
    core.run(config)
    path, manifest = snapshots(config)[0]
    os.setxattr(path / "data/files/changed.txt", "user.injected", b"bad")
    with pytest.raises(core.BackupError, match="Integrity mismatch"):
        integrity.verify(path, manifest)
    os.removexattr(path / "data/files/changed.txt", "user.injected")
    (path / "data/files/zz-extra").write_text("extra")
    with pytest.raises(core.BackupError):
        integrity.verify(path, manifest)


def test_two_sources_and_hardlink_switch_after_interrupted_run(job, tmp_path):
    source, config, _ = job
    second = tmp_path / "second"
    second.mkdir()
    (second / "file").write_text("second source")
    config.sources.append({"name": "second", "path": str(second)})
    core.run(config, now=100000)
    old, manifest = snapshots(config)[0]
    original = core.command
    calls = 0

    def fail_second(argv, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise core.BackupError("network disappeared during second source")
        return original(argv, **kwargs)

    with patch("napback.core.command", side_effect=fail_second):
        with pytest.raises(core.BackupError):
            core.run(config, force=True, now=100001)
    os.chmod(source / "unchanged.txt", 0o400)
    core.run(config, force=True, now=100002)
    assert integrity.verify(old, manifest) > 0
    assert len(snapshots(config)) == 2


def test_recursive_zfs_plan_uses_all_children_and_a_common_snapshot(job):
    _, config, _ = job
    config.host = "nas"
    config.sources = [{"name": "root", "dataset": "tank/root", "recursive": True}]
    responses = [
        "tank/root\tfilesystem\t/mnt/tank/root\ntank/root/sub\tfilesystem\t/mnt/tank/root/sub\n",
        "tank/root@auto-one\t100000\t111\ntank/root/sub@auto-one\t100000\t222\n",
        "",
        "",
    ]
    with patch.object(core.Config, "remote", side_effect=responses):
        plan = core.plan_sources(config, 100001)
    assert [source.name for source in plan] == ["root", "root/sub"]
    assert all(source.snapshot == "auto-one" for source in plan)


def test_custom_child_mountpoint_requires_explicit_source(job):
    _, config, _ = job
    config.host = "nas"
    config.sources = [{"name": "root", "dataset": "tank/root"}]
    responses = [
        "tank/root\tfilesystem\t/mnt/tank/root\ntank/root/sub\tfilesystem\t/mnt/other\n",
        "tank/root@auto-one\t100000\t111\ntank/root/sub@auto-one\t100000\t222\n",
        "",
    ]
    with patch.object(core.Config, "remote", side_effect=responses):
        with pytest.raises(core.BackupError, match="Custom child mountpoint"):
            core.plan_sources(config, 100001)


def test_missing_xattr_support_fails_init(tmp_path):
    with patch("napback.core.os.setxattr", side_effect=OSError("unsupported")):
        with pytest.raises(core.BackupError, match="hard links and user xattrs"):
            core.initialize(tmp_path / "target")
    assert not (tmp_path / "target" / core.MARKER).exists()


def test_corrupt_previous_file_is_not_reused_in_new_backup(job):
    _, config, _ = job
    core.run(config, now=100000)
    old, manifest = snapshots(config)[0]
    path = old / "data/files/changed.txt"
    timestamp = path.stat()
    path.write_text("corrupted")
    os.utime(path, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    core.run(config, force=True, now=100001)
    new, new_manifest = snapshots(config)[-1]
    assert (new / "data/files/changed.txt").read_text() == "version 1"
    assert integrity.verify(new, new_manifest) > 0
    with pytest.raises(core.BackupError):
        integrity.verify(old, manifest)


def test_named_pipe_file_restore_and_last_attempt(job, tmp_path):
    source, config, filename = job
    os.mkfifo(source / "pipe")
    core.run(config)
    assert core.status(config)["last_attempt"]["status"] == "completed"
    restored = tmp_path / "restored-special"
    assert main(["--config", str(filename), "restore", str(restored)]) == 0
    assert not (restored / "files/pipe").exists()
    with patch("napback.core.command", side_effect=core.BackupError("offline")):
        with pytest.raises(core.BackupError):
            core.run(config, force=True)
    attempt = core.status(config)["last_attempt"]
    assert attempt["status"] == "failed" and "offline" in attempt["error"]


def test_cli_commands_report_machine_readable_results(job, capsys):
    _, config, filename = job
    prefix = ["--config", str(filename)]
    assert main(prefix + ["plan"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "files"
    assert main(prefix + ["status"]) == 0
    assert json.loads(capsys.readouterr().out)["due"] is True
    assert main(prefix + ["run"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert main(prefix + ["list"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert main(prefix + ["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    assert main(prefix + ["verify", "missing"]) == 1


def test_setup_checks_sources_before_initializing_target(tmp_path, monkeypatch):
    target = tmp_path / "repository"
    configfile = tmp_path / "config.json"
    responses = iter(["my-nas", "", "y", "tank/documents", "", str(target), "files", "", "1", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    with (
        patch.object(core.Config, "remote", return_value="tank/documents\t1M\t/mnt/tank/documents"),
        patch("napback.core.plan_sources", return_value=[]),
    ):
        assert main(["--config", str(configfile), "setup", "--terminal"]) == 0
    loaded = core.Config.load(configfile)
    core.check_repository(loaded)
    assert loaded.sources == [{"name": "dataset-1", "dataset": "tank/documents", "recursive": True}]


def test_setup_snapshot_failure_creates_no_target(tmp_path, monkeypatch):
    target = tmp_path / "repository"
    configfile = tmp_path / "config.json"
    responses = iter(["my-nas", "", "y", "tank/documents", "", str(target), "files", "", "1"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    with (
        patch.object(core.Config, "remote", return_value="tank/documents\t1M\t/mnt/tank/documents"),
        patch("napback.core.plan_sources", side_effect=core.BackupError("No common snapshot")),
    ):
        assert main(["--config", str(configfile), "setup", "--terminal"]) == 1
    assert not target.exists()
    assert not configfile.exists()


def test_installer_backs_up_existing_timer_units(job, tmp_path, monkeypatch):
    _, _, configfile = job
    from napback.cli import install_timer

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    units = tmp_path / "config-home/systemd/user"
    units.mkdir(parents=True)
    original = units / "napback.service"
    original.write_text("old unit")
    with patch("napback.core.command", return_value="") as commands:
        result = install_timer(configfile)
    assert result["status"] == "timer_enabled"
    assert len(commands.call_args_list) == 2
    assert next(units.glob("napback.service.bak-*")).read_text() == "old unit"
    assert "OnCalendar=" in (units / "napback.timer").read_text()


def test_inventory_manifest_checksum_detects_tampering(job):
    _, config, _ = job
    core.run(config)
    snapshot, manifest = snapshots(config)[0]
    manifest["inventory_sha256"] = "0" * 64
    with pytest.raises(core.BackupError, match="Inventory checksum"):
        integrity.verify(snapshot, manifest)


def test_sender_has_independent_timeout(job):
    _, config, _ = job
    config.host = "nas"
    config.docker_image = "napback-source:0.1.0"
    config.io_timeout_seconds = 7
    config.timeout_seconds = 30
    argv = core.transfer_command(config, core.Source("source", "/mnt/snapshot"), Path("/tmp/out"))
    remote = next(arg for arg in argv if arg.startswith("--rsync-path="))
    assert "timeout -s TERM -k 10 30 rsync --timeout=7" in remote
    assert "--timeout=7" in argv


def test_backwards_clock_still_advances_latest_and_retention(job):
    source, config, _ = job
    config.keep = 1
    core.run(config, now=200000)
    (source / "changed.txt").write_text("after clock correction")
    core.run(config, now=100000)
    entries = snapshots(config)
    assert len(entries) == 1
    assert entries[-1][1]["completed_at"] == 100000
    assert entries[-1][1]["sequence"] == 2
    assert (config.target / "latest/files/changed.txt").read_text() == "after clock correction"
    assert not core.due(config, entries, 100001)


def test_local_transport_rejects_arbitrary_commands():
    result = subprocess.run(
        [sys.executable, "-m", "napback.local_transport", "napback-local", "echo", "unsafe"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "expected an rsync server invocation" in result.stderr
