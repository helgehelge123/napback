import copy
import json
import subprocess
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from test_raw import raw_job as raw_job

from napback import core, raw, settings_backup, vm_export


@pytest.fixture
def vm_fixture(tmp_path):
    root = tmp_path / "firmware"
    (root / "nvram").mkdir(parents=True)
    (root / "nvram/1_example_VARS.fd").write_bytes(b"synthetic-uefi-state")
    vms = [
        {
            "id": 1,
            "name": "example",
            "bootloader": "UEFI",
            "trusted_platform_module": False,
            "status": {"state": "RUNNING"},
            "devices": [
                {"attributes": {"dtype": "DISK", "path": "/dev/zvol/tank/vm"}},
                {"attributes": {"dtype": "DISPLAY", "password": "synthetic-secret"}},
            ],
        }
    ]

    def run(argv):
        if argv[-1] == "vm.query":
            return json.dumps(vms).encode()
        assert argv[-1] == "system.version"
        return b"TrueNAS-test"

    return root, vms, run


def test_vm_settings_and_firmware_are_encrypted_and_roundtrip(raw_job, vm_fixture, tmp_path):
    config, _, _, _, _ = raw_job
    root, vms, run = vm_fixture
    exported = vm_export.export(root, run)
    checked = settings_backup.validate_vms_export(exported)
    assert checked["vms"] == vms and len(checked["firmware"]) == 1
    key = tmp_path / "key"
    settings_backup.ensure_key(key)
    config.backup_truenas_vms, config.config_key_file = True, str(key)
    with patch.object(settings_backup, "export_truenas_vms", return_value=exported):
        core.run(config, now=100000)
        assert core.run(config, now=100001)["status"] == "no_new_snapshot"
        assert core.run(config, now=186400)["status"] == "completed"
    path, _ = core.completed(config)[-1]
    cipher = (path / "data/.napback-settings/truenas-vms.json.fernet").read_bytes()
    assert b"synthetic-secret" not in cipher
    assert Fernet(key.read_bytes().strip()).decrypt(cipher) == exported
    with patch.object(
        settings_backup, "export_truenas_vms", side_effect=core.BackupError("failed")
    ):
        with pytest.raises(core.BackupError):
            core.run(config, force=True, now=186401)
    assert len(core.completed(config)) == 2


@pytest.mark.parametrize(
    "case", ["missing_uefi", "active_tpm", "missing_tpm", "symlink", "changed", "oversized"]
)
def test_incomplete_vm_settings_are_rejected(vm_fixture, monkeypatch, case):
    root, vms, run = vm_fixture
    if case == "missing_uefi":
        (root / "nvram/1_example_VARS.fd").unlink()
    elif case in ("active_tpm", "missing_tpm"):
        vms[0]["trusted_platform_module"] = True
        if case == "missing_tpm":
            vms[0]["status"]["state"] = "STOPPED"
    elif case == "symlink":
        (root / "nvram/link").symlink_to(root / "nvram/1_example_VARS.fd")
    elif case == "oversized":
        monkeypatch.setattr(vm_export, "MAX_FIRMWARE", 1)
    elif case == "changed":
        original = run
        calls = 0

        def run(argv):
            nonlocal calls
            calls += 1
            if calls == 2:
                vms[0]["name"] = "changed"
            return original(argv)

    with pytest.raises(ValueError):
        vm_export.export(root, run)


def test_firmware_tampering_and_unsafe_paths_are_detected(vm_fixture):
    root, _, run = vm_fixture
    data = json.loads(vm_export.export(root, run))
    for change in [{"sha256": "0" * 64}, {"path": "../outside"}, {"size": 0}]:
        invalid = copy.deepcopy(data)
        invalid["firmware"][0].update(change)
        with pytest.raises(ValueError):
            settings_backup.validate_vms_export(json.dumps(invalid).encode())


def test_vm_export_error_does_not_echo_secrets(raw_job):
    config, *_ = raw_job
    with patch.object(
        settings_backup.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 1, b"secret", b"secret"),
    ):
        with pytest.raises(core.BackupError) as caught:
            settings_backup.export_truenas_vms(config)
    assert "secret" not in str(caught.value)


def test_raw_volume_full_incremental_and_hidden_restore(raw_job):
    config, source, available, calls, _ = raw_job
    source.kind = "volume"
    core.run(config, now=100000)
    source.snapshot, source.guid = "auto-two", "222"
    available["tank/files@auto-two"] = "222"
    core.run(config, now=100001)
    path, manifest = core.completed(config)[-1]
    assert manifest["raw_streams"][0]["kind"] == "volume"
    assert len(manifest["raw_streams"][0]["streams"]) == 2
    assert " -i " in calls[-1][-1]
    with (
        patch.object(
            core.Config,
            "remote",
            side_effect=["tank\n", "111\n", "222\n", "volume\n", "aes-256-gcm\n"],
        ),
        patch.object(core, "command", return_value="") as command,
    ):
        raw.restore(config, path, manifest, "tank/recovered-disk", None)
    assert command.call_count == 2
    for call in command.call_args_list:
        argv = call.args[0][-1]
        assert "zfs receive -u -o volmode=none" in argv
        assert "mountpoint" not in argv and "canmount" not in argv and " -F" not in argv


def test_volume_type_manifest_mismatch_is_rejected(raw_job):
    config, *_ = raw_job
    core.run(config, now=100000)
    path, manifest = core.completed(config)[-1]
    manifest["raw_streams"][0]["kind"] = "volume"
    with pytest.raises(core.BackupError, match="identity mismatch"):
        raw.validate_streams(path, manifest)


@pytest.mark.parametrize("storage", ["files", "zfs_raw"])
def test_recursive_plan_includes_volumes_or_refuses_file_mode(raw_job, storage):
    config, *_ = raw_job
    config.storage = storage
    responses = [
        "tank/files\tfilesystem\t/mnt/tank/files\ntank/files/disk\tvolume\t-\n",
        "tank/files@auto-one\t100000\t111\ntank/files/disk@auto-one\t100000\t222\n",
        "aes-256-gcm\n",
        "aes-256-gcm\n",
    ]
    # raw_job replaces the planner; use the original imported implementation.
    with patch.object(core.Config, "remote", side_effect=responses):
        if storage == "files":
            with pytest.raises(core.BackupError, match="Virtual disk"):
                ORIGINAL_PLAN(config, 100001)
        else:
            sources = ORIGINAL_PLAN(config, 100001)
            assert [(s.dataset, s.kind) for s in sources] == [
                ("tank/files", "filesystem"),
                ("tank/files/disk", "volume"),
            ]


ORIGINAL_PLAN = core.plan_sources
