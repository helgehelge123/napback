import hashlib
import sys
from unittest.mock import patch

import pytest

from napback import core, integrity, raw
from napback.cli import main


@pytest.fixture
def raw_job(tmp_path, monkeypatch):
    settings = core.initialize(tmp_path / "backup", storage="zfs_raw")
    settings.update(
        host="nas",
        sudo=True,
        sources=[{"name": "files", "dataset": "tank/files"}],
        keep=3,
        min_free_bytes=0,
    )
    config_path = tmp_path / "config.json"
    core.write_json(config_path, settings)
    config = core.Config.load(config_path)
    source = core.Source("files", "", "tank/files", "auto-one", 100000, "111")
    available = {"tank/files@auto-one": "111"}
    calls = []

    def remote(self, args):
        if "encryption" in args:
            return "aes-256-gcm\n"
        if "guid" in args:
            return available.get(args[-1], "missing") + "\n"
        if "name,guid" in args:
            return "".join(name + "\t" + guid + "\n" for name, guid in available.items())
        raise AssertionError(args)

    def send(argv, destination, **kwargs):
        # This stand-in exercises archive bookkeeping; real encryption is tested
        # separately with actual OpenZFS encrypted datasets, send and receive.
        calls.append(argv)
        data = b"synthetic stream " + source.guid.encode()
        destination.write_bytes(data)
        return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    monkeypatch.setattr(core, "plan_sources", lambda *_: [source])
    monkeypatch.setattr(core.Config, "remote", remote)
    monkeypatch.setattr(raw, "stream_to_file", send)
    return config, source, available, calls, config_path


def test_full_incremental_and_retention_keep_all_required_streams(raw_job):
    config, source, available, calls, _ = raw_job
    core.run(config, now=100000)
    old, manifest = core.completed(config)[0]
    first = next((old / "data").rglob("*.zfs"))
    inode = first.stat().st_ino
    assert "zfs send -w -p" in calls[0][-1]
    assert " -i " not in calls[0][-1]
    config.keep = 1
    source.snapshot, source.guid = "auto-two", "222"
    available["tank/files@auto-two"] = "222"
    core.run(config, now=100001)
    new, manifest = core.completed(config)[0]
    assert not old.exists()
    assert (new / first.relative_to(old)).stat().st_ino == inode
    chain = raw.validate_streams(new, manifest)["files"]["streams"]
    assert len(chain) == 2 and chain[1]["from_guid"] == "111"
    assert "-i tank/files@auto-one tank/files@auto-two" in calls[-1][-1]
    assert integrity.verify(new, manifest) == 3


@pytest.mark.parametrize("cause", ["missing_base", "recreated_base", "chain_limit"])
def test_new_full_stream_when_incremental_base_cannot_be_used(raw_job, cause):
    config, source, available, calls, _ = raw_job
    core.run(config, now=100000)
    if cause == "missing_base":
        available.clear()
    elif cause == "recreated_base":
        available["tank/files@auto-one"] = "999"
    else:
        config.raw_full_every = 1
    source.snapshot, source.guid = "auto-two", "222"
    available["tank/files@auto-two"] = "222"
    core.run(config, now=100001)
    path, manifest = core.completed(config)[-1]
    assert len(raw.validate_streams(path, manifest)["files"]["streams"]) == 1
    assert " -i " not in calls[-1][-1]


def test_raw_failure_never_publishes_or_rotates_previous_backup(raw_job):
    config, source, available, _, _ = raw_job
    config.keep = 1
    core.run(config, now=100000)
    source.snapshot, source.guid = "auto-two", "222"
    available["tank/files@auto-two"] = "222"
    with patch.object(raw, "stream_to_file", side_effect=core.BackupError("connection lost")):
        with pytest.raises(core.BackupError, match="connection lost"):
            core.run(config, now=100001)
    assert len(core.completed(config)) == 1
    assert core.status(config)["last_success"] == 100000
    core.run(config, now=100002)
    assert core.status(config)["incomplete"] == 0
    assert len(core.completed(config)) == 1


def test_raw_reuse_detects_existing_corruption(raw_job):
    config, source, available, _, _ = raw_job
    core.run(config, now=100000)
    path, _ = core.completed(config)[0]
    next((path / "data").rglob("*.zfs")).write_bytes(b"corrupt")
    source.snapshot, source.guid = "auto-two", "222"
    available["tank/files@auto-two"] = "222"
    with pytest.raises(core.BackupError, match="Integrity mismatch"):
        core.run(config, now=100001)
    assert len(core.completed(config)) == 1


def test_new_ciphertext_must_match_hash_computed_during_transfer(raw_job):
    config, _, _, _, _ = raw_job
    original = raw.stream_to_file

    def bad_disk(argv, destination, **kwargs):
        info = original(argv, destination, **kwargs)
        data = destination.read_bytes()
        destination.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        return info

    with patch.object(raw, "stream_to_file", side_effect=bad_disk):
        with pytest.raises(core.BackupError, match="Raw stream checksum mismatch"):
            core.run(config, now=100000)
    assert not core.completed(config)


def test_force_same_generation_reuses_ciphertext(raw_job):
    config, _, _, calls, _ = raw_job
    core.run(config, now=100000)
    assert core.run(config, now=100001)["status"] == "no_new_snapshot"
    core.run(config, now=100002, force=True)
    assert len(calls) == 1
    path, manifest = core.completed(config)[-1]
    raw.validate_streams(path, manifest)


def test_unencrypted_source_is_rejected_before_any_transfer(raw_job):
    config, _, _, calls, _ = raw_job
    with patch.object(core.Config, "remote", return_value="off\n"):
        with pytest.raises(core.BackupError, match="not encrypted"):
            core.run(config, now=100000)
    assert not calls and not core.completed(config)


def test_changed_snapshot_between_plan_and_send_fails_closed(raw_job):
    config, _, available, calls, _ = raw_job
    available["tank/files@auto-one"] = "999"
    with pytest.raises(core.BackupError, match="changed after planning"):
        core.run(config, now=100000)
    assert not calls


def test_incremental_base_replaced_during_send_is_not_published(raw_job):
    config, source, available, _, _ = raw_job
    core.run(config, now=100000)
    source.snapshot, source.guid = "auto-two", "222"
    available["tank/files@auto-two"] = "222"
    original = raw.stream_to_file

    def replaced(*args, **kwargs):
        result = original(*args, **kwargs)
        available["tank/files@auto-one"] = "999"
        return result

    with patch.object(raw, "stream_to_file", side_effect=replaced):
        with pytest.raises(core.BackupError, match="base changed"):
            core.run(config, now=100001)
    assert len(core.completed(config)) == 1


def test_raw_repository_cannot_be_relabelled_as_plaintext(raw_job):
    config, _, _, _, _ = raw_job
    config.storage = "files"
    with pytest.raises(core.BackupError, match="storage mismatch"):
        core.check_repository(config)


@pytest.mark.parametrize(
    "update",
    [
        {"raw_full_every": 0},
        {"raw_full_every": True},
        {"storage": "unknown"},
        {"verify": False},
        {"sources": [{"name": "files", "path": "/tmp"}]},
    ],
)
def test_invalid_raw_configuration(raw_job, update):
    _, _, _, _, path = raw_job
    data = core.read_json(path)
    data.update(update)
    core.write_json(path, data)
    with pytest.raises(core.BackupError):
        core.Config.load(path)


def test_stream_transport_detects_producer_failure_without_loading_output_in_memory(tmp_path):
    path = tmp_path / "stream"
    script = (
        "import sys; sys.stdout.buffer.write(b'x'*2000000); sys.stderr.write('failed'); sys.exit(7)"
    )
    with pytest.raises(core.BackupError, match="exited 7: failed"):
        raw.stream_to_file([sys.executable, "-c", script], path, timeout=5, io_timeout=2)
    assert path.stat().st_size == 2000000


def test_stream_transport_empty_and_timeout(tmp_path):
    with pytest.raises(core.BackupError, match="empty"):
        raw.stream_to_file(
            [sys.executable, "-c", "pass"], tmp_path / "empty", timeout=5, io_timeout=2
        )
    with pytest.raises(core.BackupError, match="timed out"):
        raw.stream_to_file(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            tmp_path / "hung",
            timeout=0.1,
            io_timeout=0.1,
        )


def test_stream_transport_binary_success_and_hash(tmp_path):
    path = tmp_path / "stream"
    result = raw.stream_to_file(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes(range(256))*1000)"],
        path,
        timeout=5,
        io_timeout=2,
    )
    assert result == {"size": 256000, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert path.stat().st_mode & 0o777 == 0o600


def test_restore_refuses_existing_dataset_without_receive(raw_job):
    config, _, _, _, _ = raw_job
    core.run(config, now=100000)
    path, manifest = core.completed(config)[0]
    with (
        patch.object(core.Config, "remote", return_value="tank\ntank/existing\n"),
        patch.object(core, "command") as command,
    ):
        with pytest.raises(core.BackupError, match="already exists"):
            raw.restore(config, path, manifest, "tank/existing", None)
        command.assert_not_called()


def test_restore_uses_unmounted_receive_without_force_and_checks_guid(raw_job):
    config, _, _, _, _ = raw_job
    core.run(config, now=100000)
    path, manifest = core.completed(config)[0]
    with (
        patch.object(core.Config, "remote", side_effect=["tank\n", "111\n", "aes-256-gcm\n"]),
        patch.object(core, "command", return_value="") as command,
    ):
        result = raw.restore(config, path, manifest, "tank/new", None)
    assert result["status"] == "restored_zfs"
    call = command.call_args
    assert "zfs receive -u -o mountpoint=none -o canmount=noauto tank/new" in call.args[0][-1]
    assert " -F" not in call.args[0][-1]
    assert call.kwargs["stdin"].closed


def test_raw_cli_verifies_and_rejects_ordinary_file_restore(raw_job, tmp_path):
    config, _, _, _, config_path = raw_job
    core.run(config, now=100000)
    assert main(["--config", str(config_path), "verify"]) == 0
    destination = tmp_path / "restore"
    assert main(["--config", str(config_path), "restore", str(destination)]) == 1
    assert not destination.exists()


def test_setup_defaults_to_native_encryption(tmp_path, monkeypatch):
    target = tmp_path / "backup"
    path = tmp_path / "config.json"
    answers = iter(["nas", "y", "tank/secret", str(target), "", "1", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    with (
        patch.object(core.Config, "remote", return_value=""),
        patch.object(core, "plan_sources", return_value=[]),
    ):
        assert main(["--config", str(path), "setup"]) == 0
    assert core.Config.load(path).storage == "zfs_raw"
    assert core.read_json(target / core.MARKER)["storage"] == "zfs_raw"


def test_raw_plan_accepts_locked_unmounted_datasets(tmp_path):
    settings = core.initialize(tmp_path / "backup", storage="zfs_raw")
    settings.update(host="nas", sources=[{"name": "files", "dataset": "tank/files"}])
    config_path = tmp_path / "config.json"
    core.write_json(config_path, settings)
    config = core.Config.load(config_path)
    responses = [
        "tank/files\tnone\ntank/files/child\t/mnt/elsewhere\n",
        "tank/files@auto-one\t100000\t111\ntank/files/child@auto-one\t100000\t222\n",
        "aes-256-gcm\n",
        "aes-256-gcm\n",
    ]
    with patch.object(core.Config, "remote", side_effect=responses):
        sources = core.plan_sources(config, 100001)
    assert len(sources) == 2 and all(source.path == "" for source in sources)
