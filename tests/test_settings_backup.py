import io
import json
import os
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from test_backup import job as job
from test_raw import raw_job as raw_job

from napback import core, integrity, settings_backup
from napback.cli import main


def encrypted_config(config, tmp_path):
    key = tmp_path / "recovery.key"
    settings_backup.ensure_key(key)
    config.backup_napback_config = True
    config.config_key_file = str(key)
    return key


def test_encrypted_settings_backup_and_explicit_restore(job, tmp_path):
    _, config, _ = job
    key = encrypted_config(config, tmp_path)
    core.run(config)
    path, manifest = core.completed(config)[-1]
    encrypted = path / "data" / settings_backup.DIRECTORY / "napback-config.json.fernet"
    assert str(config.target).encode() not in encrypted.read_bytes()
    assert key.read_bytes().strip() not in encrypted.read_bytes()
    assert integrity.verify(path, manifest) > 0
    restored = tmp_path / "restored.json"
    assert main(["decrypt-config", str(encrypted), str(restored), "--key", str(key)]) == 0
    data = json.loads(restored.read_bytes())
    assert data["sources"] == config.sources
    assert os.stat(restored).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        settings_backup.decrypt_file(encrypted, key, restored)
    wrong = tmp_path / "wrong.key"
    settings_backup.ensure_key(wrong)
    with pytest.raises(core.BackupError, match="Wrong recovery key"):
        settings_backup.decrypt_file(encrypted, wrong, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    encrypted.write_bytes(encrypted.read_bytes()[:-5] + b"xxxxx")
    with pytest.raises(core.BackupError):
        integrity.verify(path, manifest)


def test_settings_refresh_daily_without_new_snapshot_and_reuse_raw_stream(raw_job, tmp_path):
    config, _, _, calls, _ = raw_job
    encrypted_config(config, tmp_path)
    core.run(config, now=100000)
    assert core.run(config, now=100001)["status"] == "no_new_snapshot"
    assert core.run(config, now=186400)["status"] == "completed"
    assert len(calls) == 1  # no redundant ZFS send just for new settings
    assert len(core.completed(config)) == 2
    path, manifest = core.completed(config)[-1]
    assert integrity.verify(path, manifest) > 0


def test_failed_export_does_not_publish_backup_or_cleartext(job, tmp_path):
    _, config, _ = job
    encrypted_config(config, tmp_path)
    core.run(config)
    config.backup_truenas_config = True
    with patch.object(
        settings_backup, "export_truenas", side_effect=core.BackupError("export failed")
    ):
        with pytest.raises(core.BackupError, match="export failed"):
            core.run(config, force=True)
    assert len(core.completed(config)) == 1
    assert not list(config.target.rglob("*.tar"))


def test_key_permissions_and_preservation(tmp_path):
    path = tmp_path / "secret.key"
    first = settings_backup.ensure_key(path)
    assert settings_backup.ensure_key(path) == first
    path.chmod(0o644)
    with pytest.raises(core.BackupError, match="600"):
        settings_backup.key_bytes(path)
    link = tmp_path / "link.key"
    link.symlink_to(path)
    with pytest.raises(core.BackupError, match="Symlink"):
        settings_backup.ensure_key(link)


def test_trueNAS_export_validates_archive_without_disclosing_output(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as t:
        for name in ["freenas-v1.db", "pwenc_secret"]:
            content = b"sensitive-value-not-for-logs"
            info = tarfile.TarInfo(name)
            info.size = len(content)
            t.addfile(info, io.BytesIO(content))
    config = core.Config(tmp_path, "", Path("/"), [], host="nas")
    with patch.object(settings_backup.subprocess, "run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = stream.getvalue()
        assert settings_backup.export_truenas(config) == stream.getvalue()
        run.return_value.stdout = b"sensitive-value-not-for-logs"
        with pytest.raises(core.BackupError) as error:
            settings_backup.export_truenas(config)
        assert "sensitive-value" not in str(error.value)
        run.return_value.returncode = 1
        run.return_value.stderr = b"https://nas/download?token=secret"
        with pytest.raises(core.BackupError) as error:
            settings_backup.export_truenas(config)
        assert "token=secret" not in str(error.value)


def test_config_key_cannot_live_in_its_backup_target(job):
    _, config, path = job
    data = core.read_json(path)
    data.update(backup_napback_config=True, config_key_file=str(config.target / "secret.key"))
    core.write_json(path, data)
    with pytest.raises(core.BackupError, match="outside"):
        core.Config.load(path)
