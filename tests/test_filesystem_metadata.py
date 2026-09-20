"""Real ACL/xattr regressions; optional ntfs3 integration on an existing mount."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from napback import core, integrity
from napback.cli import main


def source_tree(root):
    source = root / "source"
    source.mkdir()
    (source / "sub").mkdir()
    (source / "file").write_bytes(b"0123456789abcdef")
    subprocess.run(["setfacl", "-m", "d:u::rwx,d:g::r-x,d:o::---", str(source)], check=True)
    subprocess.run(["setfacl", "-m", "u:1234:r--", str(source / "file")], check=True)
    os.setxattr(source / "file", "user.napback-regression", b"preserve me")
    subprocess.run(["setfacl", "-m", "d:u::rwx,d:g::r-x,d:o::---", str(source / "sub")], check=True)
    os.setxattr(source / "sub", "user.DOSATTRIB", b"synthetic samba attributes")
    return source


def repository(root, source):
    data = core.initialize(root / "repository")
    data.update(sources=[{"name": "fixture", "path": str(source)}], min_free_bytes=0)
    config_file = root / "config.json"
    core.write_json(config_file, data)
    return core.Config.load(config_file), config_file


@pytest.mark.skipif(not shutil.which("setfacl"), reason="setfacl required")
def test_default_and_named_acls_are_preserved_and_metadata_changes_detected(tmp_path):
    source = source_tree(tmp_path)
    config, _ = repository(tmp_path, source)
    core.run(config)
    snapshot, manifest = core.completed(config)[-1]
    destination = snapshot / "data/fixture"
    assert "user.rsync.%dacl" in os.listxattr(destination)
    assert "user.rsync.%aacl" in os.listxattr(destination / "file")
    assert "user.rsync.%dacl" in os.listxattr(destination / "sub")
    assert integrity.verify(snapshot, manifest) > 0
    planned = core.plan_sources(config, 0)[0]
    assert core.command(core.transfer_command(config, planned, destination, verify=True)) == ""
    os.setxattr(destination / "file", "user.napback-regression", b"corruption")
    assert core.command(core.transfer_command(config, planned, destination, verify=True)).strip()
    with pytest.raises(core.BackupError, match="Integrity mismatch"):
        integrity.verify(snapshot, manifest)


@pytest.mark.skipif(
    not os.environ.get("NAPBACK_TEST_NTFS_ROOT"), reason="existing ntfs3 test mount required"
)
def test_real_ntfs_backup_verification_restore_and_corruption_detection(tmp_path):
    # Explicit opt-in. Never mount, format or change existing filesystem contents.
    root = Path(
        tempfile.mkdtemp(
            prefix="napback-ntfs-regression-", dir=os.environ["NAPBACK_TEST_NTFS_ROOT"]
        )
    )
    source = source_tree(tmp_path)
    config, config_file = repository(root, source)
    core.run(config)
    snapshot, manifest = core.completed(config)[-1]
    destination = snapshot / "data/fixture"
    assert {"$LXUID", "$LXGID", "$LXMOD"} <= set(os.listxattr(destination))
    assert "user.rsync.%dacl" in os.listxattr(destination)
    assert "user.rsync.%aacl" in os.listxattr(destination / "file")
    assert "user.rsync.%dacl" in os.listxattr(destination / "sub")
    assert integrity.verify(snapshot, manifest) > 0
    planned = core.plan_sources(config, 0)[0]
    assert not core.command(core.transfer_command(config, planned, destination, verify=True))
    for restored in (tmp_path / "restored", root / "ntfs-restored"):
        assert main(["--config", str(config_file), "restore", str(restored)]) == 0
        assert (restored / "fixture/file").read_bytes() == (source / "file").read_bytes()
        assert os.getxattr(restored / "fixture/file", "user.napback-regression") == b"preserve me"
    timestamp = (destination / "file").stat()
    (destination / "file").write_bytes(b"fedcba9876543210")
    os.utime(destination / "file", ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    assert core.command(core.transfer_command(config, planned, destination, verify=True)).strip()
    with pytest.raises(core.BackupError, match="Integrity mismatch"):
        integrity.verify(snapshot, manifest)


@pytest.mark.skipif(not shutil.which("setfacl"), reason="setfacl required")
def test_acl_protection_still_updates_and_removes_source_acls(tmp_path):
    source = source_tree(tmp_path)
    config, _ = repository(tmp_path, source)
    core.run(config)
    previous, manifest = core.completed(config)[-1]
    assert "user.rsync.%dacl" in os.listxattr(previous / "data/fixture/sub")
    subprocess.run(["setfacl", "-k", str(source / "sub")], check=True)
    subprocess.run(["setfacl", "-b", str(source / "file")], check=True)
    core.run(config, force=True)
    latest, manifest = core.completed(config)[-1]
    assert "user.rsync.%dacl" not in os.listxattr(latest / "data/fixture/sub")
    assert "user.rsync.%aacl" not in os.listxattr(latest / "data/fixture/file")
    assert "user.rsync.%dacl" in os.listxattr(previous / "data/fixture/sub")
    assert integrity.verify(latest, manifest) > 0
