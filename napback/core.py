"""Transactional, pull-only, directory-based backups."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


class BackupError(Exception):
    """An actionable failure; the previous completed backup remains valid."""


class BusyError(BackupError):
    """Another process currently owns the repository lock."""


NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
SNAPSHOT_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}\Z")
MARKER = ".napback-repository.json"


def checked_name(value, label="name"):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise BackupError(f"Invalid {label}: use letters, digits, dot, dash, underscore")
    return value


def absolute(value, label="path"):
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise BackupError(f"Invalid {label}")
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise BackupError(f"{label} must be an absolute path without '..'")
    return path


def number(value, label, minimum=1):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= value < 10**15
    ):
        raise BackupError(f"{label} must be a finite number >= {minimum}")
    return value


def read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError) as error:
        raise BackupError(f"Cannot read {path}: {error}") from error


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, data):
    """Atomic replacement; data and containing directory reach stable storage."""
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def command(argv, *, timeout=120, stdin=None):
    """Run without a local shell. Kill the whole process group on timeout/interrupt."""
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
        stdin=stdin,
    )
    try:
        out, err = process.communicate(timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise
    if process.returncode:
        # Do not echo argv: configurations may contain private host/path details.
        raise BackupError(
            f"{Path(argv[0]).name} exited {process.returncode}: {err.strip()[-3000:]}"
        )
    return out


@dataclass
class Config:
    target: Path
    repository_id: str
    mountpoint: Path
    sources: list[dict]
    storage: str = "files"
    raw_full_every: int = 30
    trigger: str = "auto"
    check_interval_minutes: int = 1
    interval_hours: float = 24
    keep: int = 30
    min_free_bytes: int = 1024**3
    io_timeout_seconds: int = 120
    bandwidth_limit_kib: int = 0
    timeout_seconds: float = 86400
    verify: bool = True
    host: str | None = None
    ssh_options: list[str] | None = None
    sudo: bool = False
    docker_image: str | None = None
    snapshot_max_age_hours: float = 48
    snapshot_prefix: str = "auto-"
    label: str = "Mein Backup"
    backup_napback_config: bool = False
    backup_truenas_config: bool = False
    backup_truenas_apps: bool = False
    config_key_file: str | None = None

    @classmethod
    def load(cls, filename):
        data = read_json(filename)
        if not isinstance(data, dict):
            raise BackupError("Configuration must be a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise BackupError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
        try:
            config = cls(**data)
        except TypeError as error:
            raise BackupError(str(error)) from error
        config.target = absolute(config.target, "target")
        config.mountpoint = absolute(config.mountpoint, "mountpoint")
        if not isinstance(config.label, str) or not config.label.strip() or len(config.label) > 100:
            raise BackupError("label must contain 1 to 100 characters")
        for field in ("backup_napback_config", "backup_truenas_config", "backup_truenas_apps"):
            if not isinstance(getattr(config, field), bool):
                raise BackupError(f"{field} must be a boolean")
        if (config.backup_truenas_config or config.backup_truenas_apps) and not config.host:
            raise BackupError("TrueNAS configuration backup requires SSH")
        if config.settings_enabled():
            key_path = absolute(config.config_key_file, "config_key_file")
            if key_path == config.target or config.target in key_path.parents:
                raise BackupError("Keep the configuration recovery key outside the backup target")
        if config.storage not in ("files", "zfs_raw"):
            raise BackupError("storage must be files or zfs_raw")
        if (
            isinstance(config.raw_full_every, bool)
            or not isinstance(config.raw_full_every, int)
            or not 1 <= config.raw_full_every <= 1000
        ):
            raise BackupError("raw_full_every must be an integer from 1 to 1000")
        try:
            uuid.UUID(config.repository_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise BackupError("Invalid repository_id; initialize the target first") from error
        for field in (
            "interval_hours",
            "timeout_seconds",
            "io_timeout_seconds",
            "snapshot_max_age_hours",
        ):
            number(getattr(config, field), field)
        if config.trigger not in ("auto", "new_snapshot", "interval"):
            raise BackupError("trigger must be auto, new_snapshot or interval")
        if (
            isinstance(config.check_interval_minutes, bool)
            or not isinstance(config.check_interval_minutes, int)
            or not 1 <= config.check_interval_minutes <= 1440
        ):
            raise BackupError("check_interval_minutes must be an integer from 1 to 1440")
        for field in ("keep", "min_free_bytes", "bandwidth_limit_kib"):
            value = getattr(config, field)
            number(value, field, 0)
            if not isinstance(value, int):
                raise BackupError(f"{field} must be an integer")
        for field in ("verify", "sudo"):
            if not isinstance(getattr(config, field), bool):
                raise BackupError(f"{field} must be a boolean")
        if config.host is not None and (
            not isinstance(config.host, str)
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", config.host)
        ):
            raise BackupError(
                "host must be an SSH alias or user@hostname (configure IPv6 in ~/.ssh/config)"
            )
        if config.ssh_options is not None and (
            not isinstance(config.ssh_options, list)
            or not all(isinstance(x, str) and "\x00" not in x for x in config.ssh_options)
        ):
            raise BackupError("ssh_options must be a list of strings")
        if config.docker_image is not None:
            if (
                not config.host
                or not isinstance(config.docker_image, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", config.docker_image)
            ):
                raise BackupError("docker_image requires SSH and a valid image reference")
        if not isinstance(config.snapshot_prefix, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]*", config.snapshot_prefix
        ):
            raise BackupError("Invalid snapshot_prefix")
        if not isinstance(config.sources, list) or not config.sources:
            raise BackupError("Configure at least one source")
        names = set()
        for source in config.sources:
            if not isinstance(source, dict) or set(source) - {
                "name",
                "path",
                "dataset",
                "recursive",
                "exclude",
            }:
                raise BackupError(
                    "Each source accepts name, path OR dataset, recursive and exclude"
                )
            name = checked_name(source.get("name"), "source name")
            if name in names:
                raise BackupError(f"Duplicate source name: {name}")
            names.add(name)
            if ("path" in source) == ("dataset" in source):
                raise BackupError("Each source needs exactly one of path or dataset")
            if "path" in source:
                absolute(source["path"], "source path")
                if "recursive" in source or "exclude" in source:
                    raise BackupError("recursive and exclude are only supported for ZFS datasets")
            else:
                if (
                    not config.host
                    or not isinstance(source["dataset"], str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", source["dataset"])
                ):
                    raise BackupError("ZFS dataset sources need SSH and a valid dataset name")
                if any(part in ("", ".", "..") for part in source["dataset"].split("/")):
                    raise BackupError("Invalid dataset name")
                if not isinstance(source.get("recursive", True), bool):
                    raise BackupError("recursive must be a boolean")
                excluded = source.get("exclude", [])
                if not isinstance(excluded, list) or any(
                    not isinstance(item, str)
                    or not item.startswith(source["dataset"] + "/")
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", item)
                    or any(part in ("", ".", "..") for part in item.split("/"))
                    for item in excluded
                ):
                    raise BackupError("exclude must list child dataset names within this source")
                if excluded and not source.get("recursive", True):
                    raise BackupError("exclude requires recursive=true")
        if config.trigger == "new_snapshot" and not all(
            "dataset" in source for source in config.sources
        ):
            raise BackupError(
                "new_snapshot requires ZFS dataset sources; use auto or interval for path sources"
            )
        if config.storage == "zfs_raw" and not all(
            "dataset" in source for source in config.sources
        ):
            raise BackupError("zfs_raw storage requires ZFS dataset sources")
        if config.storage == "zfs_raw" and not config.verify:
            raise BackupError("zfs_raw storage requires verify=true")
        return config

    def effective_trigger(self):
        if self.trigger == "auto":
            return (
                "new_snapshot"
                if all("dataset" in source for source in self.sources)
                else "interval"
            )
        return self.trigger

    def fingerprint(self):
        data = asdict(self)
        for key in ("target", "mountpoint"):
            data[key] = str(data[key])
        # Retention, connection timeouts and storage thresholds do not change backup contents.
        for key in (
            "keep",
            "trigger",
            "check_interval_minutes",
            "interval_hours",
            "timeout_seconds",
            "min_free_bytes",
            "bandwidth_limit_kib",
            "io_timeout_seconds",
            "raw_full_every",
            "label",
        ):
            data.pop(key)
        if not self.backup_truenas_apps:
            data.pop("backup_truenas_apps")  # Preserve pre-0.6 fingerprints when disabled.
        if not self.settings_enabled():
            for key in ("backup_napback_config", "backup_truenas_config", "config_key_file"):
                data.pop(key)
        if self.storage == "files":
            data.pop("storage")  # Preserve existing directory-backup fingerprints.
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def settings_enabled(self):
        return self.backup_napback_config or self.backup_truenas_config or self.backup_truenas_apps

    def ssh(self):
        # Place mandatory noninteractive settings first: OpenSSH takes the first value.
        return [
            "ssh",
            "-oBatchMode=yes",
            "-oStrictHostKeyChecking=yes",
            "-oConnectTimeout=15",
            "-oServerAliveInterval=15",
            "-oServerAliveCountMax=3",
            *(self.ssh_options or []),
        ]

    def remote(self, argv):
        return command(self.remote_argv(argv), timeout=120)

    def remote_argv(self, argv):
        if self.sudo:
            argv = ["sudo", "-n", *argv]
        return [*self.ssh(), self.host, shlex.join(argv)]


def no_symlink(path):
    """Reject redirected repository paths, including existing parent components."""
    for component in (path, *path.parents):
        if component.is_symlink():
            raise BackupError(f"Symlink not allowed in repository path: {component}")


def mount_for(path):
    while not path.is_mount():
        if path == path.parent:
            raise BackupError("Cannot identify target mountpoint")
        path = path.parent
    return path


def initialize(target, storage="files"):
    if storage not in ("files", "zfs_raw"):
        raise BackupError("storage must be files or zfs_raw")
    target = absolute(str(target), "target")
    no_symlink(target)
    if target.exists() and any(target.iterdir()):
        raise BackupError("Target must be new or empty; existing data will not be adopted")
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(target, 0o700)
    probe = target / f".probe-{uuid.uuid4().hex}"
    link = probe.with_suffix(".link")
    try:
        probe.write_bytes(b"napback filesystem probe")
        os.link(probe, link)
        os.setxattr(probe, "user.napback", b"probe")
        if os.getxattr(link, "user.napback") != b"probe":
            raise BackupError("Target does not preserve extended attributes")
    except OSError as error:
        raise BackupError(
            "Target must support hard links and user xattrs (for example ext4, XFS, Btrfs)"
        ) from error
    finally:
        probe.unlink(missing_ok=True)
        link.unlink(missing_ok=True)
    repository_id = str(uuid.uuid4())
    marker = {"format": 1, "repository_id": repository_id}
    if storage == "zfs_raw":
        marker.update(format=2, storage=storage)
    write_json(target / MARKER, marker)
    (target / "snapshots").mkdir(mode=0o700)
    (target / "work").mkdir(mode=0o700)
    fsync_dir(target)
    result = {
        "target": str(target),
        "repository_id": repository_id,
        "mountpoint": str(mount_for(target)),
    }
    if storage == "zfs_raw":
        result["storage"] = storage
    return result


def check_repository(config):
    no_symlink(config.target)
    no_symlink(config.mountpoint)
    if not config.mountpoint.is_mount() or mount_for(config.target) != config.mountpoint:
        raise BackupError(f"Expected target filesystem is not mounted at {config.mountpoint}")
    marker = read_json(config.target / MARKER)
    expected = {"format": 1, "repository_id": config.repository_id}
    if config.storage == "zfs_raw":
        expected.update(format=2, storage="zfs_raw")
    if marker != expected:
        raise BackupError(
            "Repository identity mismatch or storage mismatch; use a new target when changing storage"
        )
    for name in ("snapshots", "work"):
        path = config.target / name
        if (
            path.is_symlink()
            or not path.is_dir()
            or path.stat().st_dev != config.target.stat().st_dev
        ):
            raise BackupError(f"Invalid repository directory: {path}")


@contextmanager
def locked(config):
    check_repository(config)
    fd = os.open(config.target / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BusyError("Another backup, verification or prune is running") from error
        yield
    finally:
        os.close(fd)


@dataclass
class Source:
    name: str
    path: str
    dataset: str | None = None
    snapshot: str | None = None
    created: int | None = None
    guid: str | None = None


def choose_common_snapshot(datasets, rows, prefix, now, max_age):
    candidates = {dataset: {} for dataset in datasets}
    for name, created, *_ in rows:
        dataset, tag = name.rsplit("@", 1)
        if dataset in candidates and tag.startswith(prefix):
            candidates[dataset][tag] = int(created)
    common = set.intersection(*(set(tags) for tags in candidates.values()))
    if not common:
        raise BackupError(
            "No common ZFS snapshot for all datasets; create a recursive periodic snapshot task"
        )
    tag = max(common, key=lambda x: min(values[x] for values in candidates.values()))
    created = min(values[tag] for values in candidates.values())
    if created > now + 300:
        raise BackupError("ZFS snapshot is in the future; check the NAS/PC clocks")
    if now - created > max_age:
        raise BackupError("Newest common ZFS snapshot is too old; check the NAS snapshot task")
    return tag, candidates


def plan_sources(config, now):
    result = []
    for source in config.sources:
        if "path" in source:
            path = str(absolute(source["path"]))
            if not config.host:
                resolved = Path(path).resolve(strict=True)
                target = config.target.resolve()
                if resolved == target or resolved in target.parents or target in resolved.parents:
                    raise BackupError("Local source and backup target must not overlap")
                if not resolved.is_dir():
                    raise BackupError("Source must be a directory")
            result.append(Source(source["name"], path))
            continue
        root = source["dataset"]
        depth = ["-r"] if source.get("recursive", True) else ["-d", "0"]
        output = config.remote(
            ["zfs", "list", "-H", "-p", "-t", "filesystem", "-o", "name,mountpoint", *depth, root]
        )
        datasets = {}
        for line in output.splitlines():
            dataset, mountpoint = line.split("\t")
            if dataset != root and not dataset.startswith(root + "/"):
                raise BackupError("Unexpected dataset in ZFS response")
            if any(
                dataset == item or dataset.startswith(item + "/")
                for item in source.get("exclude", [])
            ):
                continue
            if config.storage == "files" and mountpoint in ("none", "legacy", "-"):
                raise BackupError(f"Dataset {dataset} needs a normal mounted filesystem")
            if config.storage == "files":
                absolute(mountpoint, "ZFS mountpoint")
            datasets[dataset] = mountpoint
        if root not in datasets:
            raise BackupError("Requested dataset not found")
        output = config.remote(
            ["zfs", "list", "-H", "-p", "-t", "snapshot", "-o", "name,creation,guid", "-r", root]
        )
        rows = [line.split("\t") for line in output.splitlines() if line]
        if any(len(row) != 3 or not row[2].isdigit() for row in rows):
            raise BackupError("Invalid ZFS snapshot GUID response")
        guids = {row[0]: row[2] for row in rows}
        tag, timestamps = choose_common_snapshot(
            datasets, rows, config.snapshot_prefix, now, config.snapshot_max_age_hours * 3600
        )
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", tag):
            raise BackupError("Unsupported ZFS snapshot name")
        for dataset in sorted(datasets, key=lambda x: (x.count("/"), x)):
            relative = dataset[len(root) :].lstrip("/")
            # Snapshot trees contain mountpoint placeholders. A custom mountpoint cannot
            # be reconstructed by dataset name; fail instead of silently restoring wrong paths.
            if (
                config.storage == "files"
                and relative
                and datasets[dataset] != datasets[root] + "/" + relative
            ):
                raise BackupError(
                    f"Custom child mountpoint for {dataset}; configure it as a separate source"
                )
            name = source["name"] + ("/" + relative if relative else "")
            path = datasets[dataset] + "/.zfs/snapshot/" + tag if config.storage == "files" else ""
            result.append(
                Source(
                    name, path, dataset, tag, timestamps[dataset][tag], guids[dataset + "@" + tag]
                )
            )
    if config.storage == "zfs_raw":
        from .raw import require_encryption

        require_encryption(config, result)
    return result


def transfer_command(config, source, destination, previous=None, verify=False):
    argv = [
        "rsync",
        "-aHAX",
        "--numeric-ids",
        "--fake-super",
        "--one-file-system",
        "--checksum",
        "--exclude=.zfs/",
        "--timeout=" + str(int(config.io_timeout_seconds)),
        "--protect-args",
        # ntfs3 maintains these WSL bookkeeping attributes from chmod/chown.
        # Removing them during xattr reconciliation causes persistent changes
        # and can also discard the root's fake-super default ACL. Protect only
        # these receiver attributes; source ACLs/xattrs and checksums still apply.
        "--filter=-xr $LXUID",
        "--filter=-xr $LXGID",
        "--filter=-xr $LXMOD",
        "--filter=-xr $LXDEV",
        # ACL handling owns these two fake-super fields. Ordinary xattr
        # reconciliation must not delete them when a directory also has user
        # attributes (for example Samba's user.DOSATTRIB).
        "--filter=-xr user.rsync.%aacl",
        "--filter=-xr user.rsync.%dacl",
    ]
    if config.bandwidth_limit_kib:
        argv += ["--bwlimit=" + str(config.bandwidth_limit_kib)]
    if verify:
        argv += ["--dry-run", "--delete", "--itemize-changes", "--out-format=%i %n%L"]
    else:
        argv += ["--fsync", "--sparse"]
        if previous and previous.is_dir():
            argv += ["--link-dest=" + str(previous)]
    path = source.path.rstrip("/") + "/"
    if config.host:
        remote = ["sudo", "-n"] if config.sudo else []
        if config.docker_image:
            if any(c in source.path for c in ",\r\n"):
                raise BackupError("Docker source paths cannot contain comma or newline")
            remote += [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--cap-add=DAC_READ_SEARCH",
                "--security-opt=no-new-privileges",
                "--mount",
                f"type=bind,src={source.path},dst=/source,readonly",
                config.docker_image,
                "timeout",
                "-s",
                "TERM",
                "-k",
                "10",
                str(int(config.timeout_seconds)),
                "rsync",
                "--timeout=" + str(int(config.io_timeout_seconds)),
            ]
            path = "/source/"
        else:
            remote += ["rsync", "--timeout=" + str(int(config.io_timeout_seconds))]
        argv += ["-e", shlex.join(config.ssh()), "--rsync-path=" + shlex.join(remote)]
        path = config.host + ":" + path
    else:
        argv += ["-e", shlex.join([sys.executable, "-m", "napback.local_transport"])]
        path = "napback-local:" + path
    argv += ["--", path, str(destination) + "/"]
    return argv


def completed(config):
    entries = []
    for path in (config.target / "snapshots").iterdir():
        if not SNAPSHOT_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            raise BackupError(f"Unexpected entry in snapshots directory: {path.name}")
        manifest = read_json(path / "manifest.json")
        if (
            not isinstance(manifest, dict)
            or manifest.get("repository_id") != config.repository_id
            or manifest.get("id") != path.name
        ):
            raise BackupError(f"Invalid snapshot manifest: {path.name}")
        number(manifest.get("completed_at"), "completed_at", 0)
        sequence = manifest.get("sequence", 0)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise BackupError("Invalid snapshot sequence")
        entries.append((path, manifest))
    return sorted(
        entries,
        key=lambda item: (item[1].get("sequence", 0), item[1]["completed_at"], item[0].name),
    )


def due(config, entries, now):
    matches = [
        item for _, item in entries if item.get("config_fingerprint") == config.fingerprint()
    ]
    if not matches:
        return True
    elapsed = now - matches[-1]["completed_at"]
    # A backwards clock jump must not postpone backups indefinitely.
    return elapsed < -300 or elapsed >= config.interval_hours * 3600


def sync_tree(path):
    """rsync --fsync flushes files; flush every directory before publication."""
    for directory, _, _ in os.walk(path, followlinks=False, topdown=False):
        fsync_dir(directory)


def clean_work(config):
    for path in (config.target / "work").iterdir():
        if not SNAPSHOT_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            raise BackupError(f"Unexpected work directory: {path.name}; inspect manually")
        owner = read_json(path / "owner.json")
        if owner != {"repository_id": config.repository_id, "id": path.name}:
            raise BackupError("Refusing to remove an unrecognized incomplete backup")
        shutil.rmtree(path)


def prune(config, entries):
    if config.keep == 0:
        return
    for path, _ in entries[: -config.keep]:
        # Move to the owned work area first: a crash during deletion cannot leave
        # a half-deleted entry that looks like a completed snapshot.
        trash = config.target / "work" / path.name
        os.rename(path, trash)
        fsync_dir(config.target / "snapshots")
        fsync_dir(config.target / "work")
        shutil.rmtree(trash)
    fsync_dir(config.target / "work")


def update_latest(config, entries):
    if not entries:
        return
    identifier = entries[-1][0].name
    destination = Path("snapshots") / identifier / "data"
    current = config.target / "latest"
    if current.is_symlink() and os.readlink(current) == str(destination):
        return
    if current.exists() and not current.is_symlink():
        raise BackupError("Unexpected non-symlink at latest; inspect manually")
    temporary = config.target / f".latest-{uuid.uuid4().hex}"
    temporary.symlink_to(destination)
    os.replace(temporary, current)
    fsync_dir(config.target)


@contextmanager
def attempt(config, started):
    state = {"started_at": started, "status": "running"}
    write_json(config.target / "last-attempt.json", state)
    try:
        yield
    except BaseException as error:
        state.update(status="failed", error=str(error)[-3000:], ended_at=time.time())
        with contextlib.suppress(OSError, BackupError):
            write_json(config.target / "last-attempt.json", state)
        raise
    else:
        state.update(status="completed", ended_at=time.time())
        write_json(config.target / "last-attempt.json", state)


def source_signature(sources):
    """GUIDs distinguish even snapshots recreated with the same name."""
    items = [asdict(item) if isinstance(item, Source) else item for item in sources]
    return sorted(
        (
            item["name"],
            item.get("dataset"),
            item.get("guid"),
            item.get("snapshot"),
            item.get("created"),
        )
        for item in items
    )


def same_sources(config, entries, sources):
    matches = [
        manifest
        for _, manifest in entries
        if manifest.get("config_fingerprint") == config.fingerprint()
    ]
    return bool(matches and source_signature(matches[-1]["sources"]) == source_signature(sources))


def scheduled_check_due(config, now):
    path = config.target / "last-check.json"
    if not path.exists():
        return True
    state = read_json(path)
    if state.get("config_fingerprint") != config.fingerprint():
        return True
    elapsed = now - state["started_at"]
    return elapsed < -300 or elapsed >= config.check_interval_minutes * 60


@contextmanager
def checking(config, started):
    state = {
        "started_at": started,
        "status": "checking",
        "config_fingerprint": config.fingerprint(),
    }
    write_json(config.target / "last-check.json", state)
    try:
        yield state
    except BaseException as error:
        state.update(status="failed", error=str(error)[-3000:])
        raise
    finally:
        state["ended_at"] = time.time()
        with contextlib.suppress(OSError, BackupError):
            write_json(config.target / "last-check.json", state)


def run(config, *, force=False, now=None, scheduled=False):
    started = time.time() if now is None else now
    with locked(config):
        entries = completed(config)
        update_latest(config, entries)
        if scheduled and not force and not scheduled_check_due(config, started):
            return {"status": "check_not_due"}
        with checking(config, started) as check_state:
            return _checked_run(config, entries, check_state, force, started, now)


def _checked_run(config, entries, check_state, force, started, now):
    snapshot_mode = config.effective_trigger() == "new_snapshot"
    sources = None
    if not force and not snapshot_mode and not due(config, entries, started):
        check_state["status"] = "not_due"
        return {"status": "not_due", "last_success": entries[-1][1]["completed_at"]}
    if snapshot_mode:
        sources = plan_sources(config, started)
        settings_due = config.settings_enabled() and (
            not entries or started - entries[-1][1]["completed_at"] >= 86400
        )
        if not force and not settings_due and same_sources(config, entries, sources):
            check_state["status"] = "no_new_snapshot"
            check_state["sources"] = [asdict(source) for source in sources]
            return {"status": "no_new_snapshot", "last_success": entries[-1][1]["completed_at"]}
    with attempt(config, started):
        clean_work(config)
        if shutil.disk_usage(config.target).free < config.min_free_bytes:
            raise BackupError("Not enough free space; no completed snapshots were deleted")
        if sources is None:
            sources = plan_sources(config, started)
        check_state["status"] = "running"
        write_json(config.target / "last-check.json", check_state)
        identifier = (
            datetime.fromtimestamp(started, timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
            + uuid.uuid4().hex[:12]
        )
        stage = config.target / "work" / identifier
        stage.mkdir(mode=0o700)
        write_json(stage / "owner.json", {"repository_id": config.repository_id, "id": identifier})
        previous = entries[-1][0] / "data" if entries else None
        raw_streams = None
        if config.storage == "zfs_raw":
            from .raw import backup_streams

            raw_streams = backup_streams(config, sources, stage, entries)
        for source in sources if config.storage == "files" else []:
            if source.dataset:
                config.remote(
                    ["test", "-d", source.path]
                )  # Trigger host automount only for actual transfers.
            destination = stage / "data" / source.name
            # A source may contain a symlink where a child dataset belongs.
            no_symlink(destination)
            destination.mkdir(parents=True, exist_ok=True)
            command(
                transfer_command(
                    config, source, destination, previous / source.name if previous else None
                ),
                timeout=config.timeout_seconds,
            )
            if config.verify:
                changes = command(
                    transfer_command(config, source, destination, verify=True),
                    timeout=config.timeout_seconds,
                )
                if changes.strip():
                    raise BackupError(
                        f"Verification failed for {source.dataset or source.name}: {changes[:1500]}"
                    )
        from .integrity import record

        if config.settings_enabled():
            from .settings_backup import backup_settings

            backup_settings(config, stage)
        inventory_sha256 = record(stage)
        finished = time.time() if now is None else now
        manifest = {
            "sequence": max((item.get("sequence", 0) for _, item in entries), default=0) + 1,
            "inventory_sha256": inventory_sha256,
            "format": 1,
            "id": identifier,
            "repository_id": config.repository_id,
            "config_fingerprint": config.fingerprint(),
            "started_at": started,
            "completed_at": finished,
            "verified": config.verify,
            "sources": [asdict(source) for source in sources],
        }
        if raw_streams is not None:
            manifest.update(storage="zfs_raw", raw_streams=raw_streams)
        write_json(stage / "manifest.json", manifest)
        if raw_streams is not None:
            from .integrity import verify
            from .raw import validate_streams

            verify(stage, manifest)  # Read ciphertext back before publication.
            validate_streams(stage, manifest)  # Compare readback to the in-flight stream hashes.
        sync_tree(stage)
        final = config.target / "snapshots" / identifier
        check_repository(config)
        os.rename(stage, final)
        fsync_dir(config.target / "snapshots")
        fsync_dir(config.target / "work")
        # The manifest is the success record; no fragile separate success timestamp.
        entries = completed(config)
        update_latest(config, entries)
        prune(config, entries)
        check_state["status"] = "completed"
        return {
            "status": "completed",
            "id": identifier,
            "last_success": finished,
            "verified": config.verify,
            "sources": len(sources),
        }


def _status_data(config, running=False):
    attempt_path = config.target / "last-attempt.json"
    check_path = config.target / "last-check.json"
    last_attempt = read_json(attempt_path) if attempt_path.exists() else None
    last_check = read_json(check_path) if check_path.exists() else None
    if not running and last_attempt and last_attempt.get("status") == "running":
        last_attempt["status"] = "interrupted"
    if not running and last_check and last_check.get("status") in ("checking", "running"):
        last_check["status"] = "interrupted"
    result = {
        "running": running,
        "last_attempt": last_attempt,
        "last_check": last_check,
        "target": str(config.target),
        "trigger": config.effective_trigger(),
        "check_interval_minutes": config.check_interval_minutes,
        "storage": config.storage,
    }
    if running:
        return result  # Published directories can change while another process owns the lock.
    entries = completed(config)
    result.update(
        snapshots=len(entries),
        last_success=entries[-1][1]["completed_at"] if entries else None,
        due=due(config, entries, time.time()) if config.effective_trigger() == "interval" else None,
        incomplete=len(list((config.target / "work").iterdir())),
    )
    return result


def status(config):
    try:
        with locked(config):
            return _status_data(config)
    except BusyError:
        return _status_data(config, running=True)
