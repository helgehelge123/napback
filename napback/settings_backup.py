"""Encrypted settings exports. Plaintext exports are kept in memory, never staged."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken

from . import core

MAX_EXPORT = 64 * 1024 * 1024
DIRECTORY = ".napback-settings"

# Runs over the existing SSH connection. Download tokens never leave the NAS.
# config.save is the official export, including the password secret seed.
EXPORT_SCRIPT = r"""
import json, ssl, subprocess, sys, urllib.request
try:
    def call(*args):
        p = subprocess.run(['midclt', 'call', *args], capture_output=True, timeout=120)
        if p.returncode: raise RuntimeError('middleware export failed')
        return json.loads(p.stdout)
    settings = call('system.general.config')
    job, path = call('core.download', 'config.save',
                     '[{"secretseed":true,"pool_keys":false,"root_authorized_keys":true}]',
                     'truenas-config.tar')
    if not path.startswith('/_download/') or '\\' in path:
        raise RuntimeError('invalid download path')
    https = settings.get('ui_httpsredirect', False)
    port = settings.get('ui_httpsport', 443) if https else settings.get('ui_port', 80)
    url = ('https' if https else 'http') + '://127.0.0.1:' + str(int(port)) + path
    # The connection is NAS loopback inside authenticated SSH, often with a self-signed cert.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise RuntimeError('unexpected redirect')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    with opener.open(url, timeout=120) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024: raise RuntimeError('export too large')
    jobs = call('core.get_jobs', json.dumps([['id', '=', job]]))
    if not jobs or jobs[0]['state'] != 'SUCCESS': raise RuntimeError('export job failed')
    sys.stdout.buffer.write(data)
except Exception:
    sys.stderr.write('TrueNAS configuration export failed. Check administrative access and the local web service.\n')
    sys.exit(1)
"""


def key_bytes(path, *, require_private=True):
    path = Path(path)
    core.no_symlink(path)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or (require_private and mode & 0o077):
        raise core.BackupError("Der Konfigurationsschlüssel braucht Dateirechte 600.")
    data = path.read_bytes().strip()
    try:
        Fernet(data)
    except (ValueError, TypeError) as error:
        raise core.BackupError("Ungültiger Konfigurationsschlüssel.") from error
    return data


def ensure_key(path):
    path = Path(path)
    core.no_symlink(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return key_bytes(path)
    with os.fdopen(fd, "wb") as stream:
        stream.write(Fernet.generate_key() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    core.fsync_dir(path.parent)
    return key_bytes(path)


def export_truenas(config):
    result = subprocess.run(
        config.remote_argv(["python3", "-c", EXPORT_SCRIPT]), capture_output=True, timeout=180
    )
    if result.returncode:
        # Never echo export output, token-bearing URLs or remote stderr into logs.
        raise core.BackupError(
            "TrueNAS-Konfiguration konnte nicht exportiert werden. Prüfe Administratorrechte und den lokalen TrueNAS-Webdienst."
        )
    data = result.stdout
    if not data or len(data) > MAX_EXPORT:
        raise core.BackupError("TrueNAS-Konfiguration ist leer oder größer als 64 MiB.")
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            names = {m.name for m in archive if m.isfile()}
            if not {"freenas-v1.db", "pwenc_secret"} <= names:
                raise ValueError("missing settings")
    except (tarfile.TarError, ValueError) as error:
        raise core.BackupError(
            "TrueNAS lieferte kein vollständiges Konfigurationsarchiv."
        ) from error
    return data


def validate_apps_export(data):
    """Check every archived file without extracting sensitive configuration."""
    from .app_export import MAX_CONTENT, MAX_FILES

    actual, manifest, total = {}, None, 0
    if not data or len(data) > MAX_EXPORT:
        raise ValueError("Invalid app archive size")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            total += member.size
            if (
                not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != member.name
                or member.name in actual
                or member.size > MAX_EXPORT
                or total > MAX_CONTENT + MAX_EXPORT
                or len(actual) > MAX_FILES
            ):
                raise ValueError("Unsafe or oversized app archive")
            stream = archive.extractfile(member)
            if member.name == "manifest.json":
                if manifest is not None:
                    raise ValueError("Duplicate app manifest")
                manifest = json.load(stream)
            else:
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                actual[member.name] = {
                    "path": member.name,
                    "size": member.size,
                    "sha256": digest.hexdigest(),
                }
    required = {
        "docker/containers.json",
        "docker/images.json",
        "docker/networks.json",
        "docker/volumes.json",
        "compose/projects.json",
        "system.json",
    }
    if not manifest or manifest.get("format") != 1 or not required <= set(actual):
        raise ValueError("Incomplete app archive")
    expected = manifest.get("files")
    if not isinstance(expected, list) or len(expected) != len(actual):
        raise ValueError("Invalid app inventory")
    if len({e["path"] for e in expected}) != len(expected) or any(
        actual.get(e["path"]) != e for e in expected
    ):
        raise ValueError("App inventory checksum mismatch")
    return manifest


def export_truenas_apps(config):
    script = Path(__file__).with_name("app_export.py").read_text()
    result = subprocess.run(
        config.remote_argv(["python3", "-"]),
        input=script.encode(),
        capture_output=True,
        timeout=600,
    )
    try:
        if result.returncode:
            raise ValueError("Remote export failed")
        validate_apps_export(result.stdout)
    except Exception as error:
        raise core.BackupError(
            "App-Einrichtung konnte nicht vollständig gesichert werden. Prüfe NAS-Zugang, Docker Compose und ob gerade ein App-Update läuft."
        ) from error
    return result.stdout


def backup_settings(config, stage):
    cipher = Fernet(key_bytes(config.config_key_file))
    directory = stage / "data" / DIRECTORY
    directory.mkdir(mode=0o700)
    exports = {}
    if config.backup_napback_config:
        data = asdict(config)
        data["target"], data["mountpoint"] = str(config.target), str(config.mountpoint)
        exports["napback-config.json.fernet"] = json.dumps(data, indent=2).encode()
    if config.backup_truenas_config:
        exports["truenas-config.tar.fernet"] = export_truenas(config)
    if config.backup_truenas_apps:
        exports["truenas-apps.tar.fernet"] = export_truenas_apps(config)
    for name, cleartext in exports.items():
        encrypted = cipher.encrypt(cleartext)
        if cipher.decrypt(encrypted) != cleartext:
            raise core.BackupError("Configuration encryption roundtrip failed")
        with (directory / name).open("xb") as stream:
            os.chmod(directory / name, 0o600)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
    return list(exports)


def decrypt_file(source, key, destination):
    """Explicit recovery to a new private file; never apply NAS or timer settings."""
    source, destination = (
        Path(source),
        core.absolute(str(Path(destination).expanduser().absolute()), "destination"),
    )
    core.no_symlink(destination)
    if source.stat().st_size > MAX_EXPORT * 2:
        raise core.BackupError("Encrypted configuration exceeds size limit")
    try:
        cleartext = Fernet(key_bytes(key, require_private=False)).decrypt(source.read_bytes())
    except InvalidToken as error:
        raise core.BackupError("Wrong recovery key or damaged configuration archive") from error
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(cleartext)
        stream.flush()
        os.fsync(stream.fileno())
    core.fsync_dir(destination.parent)
    return {"status": "decrypted", "destination": str(destination)}
