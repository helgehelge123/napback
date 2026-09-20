import io
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from test_backup import job as job
from test_raw import raw_job as raw_job

from napback import app_export, core, integrity, settings_backup


@pytest.fixture
def apps(tmp_path):
    root = tmp_path / "ix-apps"
    version = root / "app_configs/example/versions/1.2"
    version.mkdir(parents=True)
    (version / "user_config.yaml").write_text("password: synthetic-secret\n")
    (root / "metadata.yaml").write_text("example: 1.2\n")
    external = tmp_path / "external"
    external.mkdir()
    compose = external / "compose.yaml"
    compose.write_text("services: {demo: {image: example:1.2}}\n")
    for name in (".env", "service.env", "secret.txt"):
        (external / name).write_text("synthetic-private-value\n")
    deployed = [
        {
            "Id": "a",
            "Image": "sha256:image",
            "Name": "/example",
            "Config": {
                "Labels": {
                    "com.docker.compose.project.config_files": str(compose),
                    "com.docker.compose.project.working_dir": str(external),
                }
            },
            "HostConfig": {},
            "Mounts": [],
        }
    ]
    resolved = {
        "services": {
            "demo": {"image": "example:1.2", "environment": {"VALUE": "synthetic-private-value"}}
        },
        "secrets": {"token": {"file": str(external / "secret.txt")}},
    }

    def run(argv):
        if argv == ["docker", "ps", "-aq"]:
            return b"a\n"
        if argv[:4] == ["docker", "inspect", "--type", "container"]:
            return json.dumps(deployed).encode()
        if argv[:3] == ["docker", "image", "inspect"]:
            return b'[{"RepoDigests":["example@sha256:immutable"]}]'
        if argv[1] in ("network", "volume"):
            return b""
        if argv[:2] == ["docker", "compose"]:
            if "--no-env-resolution" in argv:
                return json.dumps(
                    {"services": {"demo": {"env_file": [{"path": str(external / "service.env")}]}}}
                ).encode()
            return json.dumps(resolved).encode()
        if argv == ["midclt", "call", "system.version"]:
            return b'"TrueNAS-test"'
        raise AssertionError(argv)

    return root, external, run, deployed


def test_complete_export_includes_versions_credentials_and_external_compose(apps):
    root, external, run, _ = apps
    data = app_export.export(root, run)
    manifest = settings_backup.validate_apps_export(data)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for path in [
            root / "metadata.yaml",
            root / "app_configs/example/versions/1.2/user_config.yaml",
            external / "compose.yaml",
            external / ".env",
            external / "service.env",
            external / "secret.txt",
        ]:
            name = "files/" + str(path).lstrip("/")
            assert archive.extractfile(name).read() == path.read_bytes()
        assert json.load(archive.extractfile("docker/images.json"))[0]["RepoDigests"]
        assert len(json.load(archive.extractfile("compose/projects.json"))) == 1
    assert len(manifest["files"]) > 6


def test_export_rejects_changed_deployment(apps):
    root, _, run, deployed = apps
    calls = 0

    def changing(argv):
        nonlocal calls
        if argv[:4] == ["docker", "inspect", "--type", "container"]:
            calls += 1
            if calls == 2:
                deployed[0]["Image"] = "sha256:new-image"
        return run(argv)

    with pytest.raises(ValueError, match="Containers changed"):
        app_export.export(root, changing)


def test_container_mount_order_does_not_hide_real_mount_changes():
    a = {
        "Id": "a",
        "Mounts": [
            {"Destination": "/a", "Source": "/data/a"},
            {"Destination": "/b", "Source": "/data/b"},
        ],
    }
    b = {"Id": "a", "Mounts": list(reversed(a["Mounts"]))}
    assert app_export.container_signature([a]) == app_export.container_signature([b])
    b["Mounts"] = [{"Destination": "/a", "Source": "/different"}, a["Mounts"][1]]
    assert app_export.container_signature([a]) != app_export.container_signature([b])


def test_export_rejects_changed_files_and_symlinks(apps):
    root, _, run, _ = apps

    def changing(argv):
        if argv[:2] == ["midclt", "call"]:
            (root / "metadata.yaml").write_text("changed while reading")
        return run(argv)

    with pytest.raises(ValueError, match="changed"):
        app_export.export(root, changing)
    (root / "app_configs/unsafe").symlink_to(root / "metadata.yaml")
    with pytest.raises(ValueError, match="Symlinks"):
        app_export.export(root, run)


def test_missing_referenced_secret_aborts_instead_of_skipping(apps):
    root, external, run, _ = apps
    (external / "secret.txt").rename(external / "missing-secret.txt")
    with pytest.raises(FileNotFoundError):
        app_export.export(root, run)


def test_unreadable_directory_is_not_silently_omitted(apps, monkeypatch):
    root, _, run, _ = apps

    def broken_walk(path, **options):
        options["onerror"](PermissionError("unreadable directory"))
        return iter(())

    monkeypatch.setattr(app_export.os, "walk", broken_walk)
    with pytest.raises(PermissionError):
        app_export.export(root, run)


def test_oversized_configuration_fails_closed(apps, monkeypatch):
    root, _, run, _ = apps
    monkeypatch.setattr(app_export, "MAX_EXPORT", 5)
    with pytest.raises(ValueError, match="oversized"):
        app_export.export(root, run)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("docker/containers.json", tarfile.REGTYPE),
    ],
)
def test_archive_rejects_traversal_links_and_duplicate_members(apps, name, kind):
    root, _, run, _ = apps
    original = app_export.export(root, run)
    result = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(original), mode="r:gz") as source,
        tarfile.open(fileobj=result, mode="w:gz") as target,
    ):
        for member in source:
            target.addfile(member, source.extractfile(member))
        info = tarfile.TarInfo(name)
        info.type, info.size = kind, 0
        target.addfile(info, io.BytesIO())
    with pytest.raises(ValueError):
        settings_backup.validate_apps_export(result.getvalue())


def test_app_export_encrypted_and_restorable_as_only_config_option(job, tmp_path, apps):
    _, config, _ = job
    root, _, run, _ = apps
    data = app_export.export(root, run)
    key = tmp_path / "config.key"
    settings_backup.ensure_key(key)
    config.backup_truenas_apps = True
    config.config_key_file = str(key)
    with patch.object(settings_backup, "export_truenas_apps", return_value=data):
        core.run(config)
    path, manifest = core.completed(config)[-1]
    assert integrity.verify(path, manifest) > 0
    encrypted = path / "data/.napback-settings/truenas-apps.tar.fernet"
    assert b"synthetic-private-value" not in encrypted.read_bytes()
    assert not list(config.target.rglob("*.tar"))
    restored = tmp_path / "restored.tar"
    settings_backup.decrypt_file(encrypted, key, restored)
    assert restored.read_bytes() == data
    assert settings_backup.validate_apps_export(restored.read_bytes())


def test_apps_only_refresh_daily_and_failed_export_keeps_previous(raw_job, tmp_path):
    config, _, _, calls, _ = raw_job
    key = tmp_path / "key"
    settings_backup.ensure_key(key)
    config.backup_truenas_apps, config.config_key_file = True, str(key)
    with patch.object(settings_backup, "export_truenas_apps", return_value=b"export"):
        core.run(config, now=100000)
        assert core.run(config, now=100001)["status"] == "no_new_snapshot"
        assert core.run(config, now=186400)["status"] == "completed"
    assert len(calls) == 1
    with patch.object(
        settings_backup, "export_truenas_apps", side_effect=core.BackupError("export failed")
    ):
        with pytest.raises(core.BackupError, match="export failed"):
            core.run(config, force=True, now=186401)
    assert len(core.completed(config)) == 2
    path, _ = core.completed(config)[-1]
    assert (
        Fernet(key.read_bytes().strip()).decrypt(
            (path / "data/.napback-settings/truenas-apps.tar.fernet").read_bytes()
        )
        == b"export"
    )


def test_export_errors_never_echo_remote_secrets(tmp_path):
    config = core.Config(tmp_path, "", Path("/"), [], host="nas")
    response = subprocess.CompletedProcess([], 1, b"private-config-value", b"private-stderr-value")
    with patch.object(settings_backup.subprocess, "run", return_value=response):
        with pytest.raises(core.BackupError) as caught:
            settings_backup.export_truenas_apps(config)
    assert "private" not in str(caught.value)


def test_apps_config_requires_boolean_ssh_and_external_key(job, tmp_path):
    _, _, filename = job
    data = core.read_json(filename)
    data["backup_truenas_apps"] = 1
    core.write_json(filename, data)
    with pytest.raises(core.BackupError, match="boolean"):
        core.Config.load(filename)
    data["backup_truenas_apps"] = True
    core.write_json(filename, data)
    with pytest.raises(core.BackupError, match="SSH"):
        core.Config.load(filename)
    data.update(host="nas", config_key_file=str(tmp_path / "key"))
    core.write_json(filename, data)
    assert core.Config.load(filename).settings_enabled()
