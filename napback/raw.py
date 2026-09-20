"""Native encrypted ZFS streams, without local ZFS or encryption keys."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import selectors
import signal
import subprocess
import time

from . import core, integrity


def require_encryption(config, sources):
    for source in sources:
        value = config.remote(
            ["zfs", "get", "-H", "-o", "value", "encryption", source.dataset]
        ).strip()
        if value not in (
            "aes-128-ccm",
            "aes-192-ccm",
            "aes-256-ccm",
            "aes-128-gcm",
            "aes-192-gcm",
            "aes-256-gcm",
        ):
            raise core.BackupError(
                f"Dataset {source.dataset} is not encrypted; refusing an unencrypted raw backup"
            )


def stream_to_file(argv, destination, *, timeout, io_timeout, bandwidth_limit_kib=0):
    """Bound memory, capture stderr, and never accept a failed or empty producer."""
    digest = hashlib.sha256()
    size = 0
    errors = bytearray()
    started = last_output = time.monotonic()
    with destination.open("xb") as output:
        os.chmod(destination, 0o600)
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stderr, selectors.EVENT_READ)
                while selector.get_map():
                    now = time.monotonic()
                    if now - started > timeout or now - last_output > io_timeout:
                        raise core.BackupError("ZFS stream transfer timed out")
                    for key, _ in selector.select(min(0.25, io_timeout)):
                        block = os.read(key.fd, 256 * 1024)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        if key.fileobj is process.stderr:
                            errors.extend(block)
                            del errors[:-3000]
                        else:
                            output.write(block)
                            digest.update(block)
                            size += len(block)
                            last_output = time.monotonic()
                            if bandwidth_limit_kib:
                                delay = size / (bandwidth_limit_kib * 1024) - (
                                    last_output - started
                                )
                                # Short sleeps keep interruption and total timeout responsive.
                                while delay > 0:
                                    if time.monotonic() - started > timeout:
                                        raise core.BackupError("ZFS stream transfer timed out")
                                    time.sleep(min(delay, 0.25))
                                    delay = size / (bandwidth_limit_kib * 1024) - (
                                        time.monotonic() - started
                                    )
                                last_output = time.monotonic()
            process.wait(timeout=max(0.1, timeout - (time.monotonic() - started)))
            if process.returncode:
                raise core.BackupError(
                    f"ZFS stream sender exited {process.returncode}: {errors.decode(errors='replace').strip()}"
                )
            if size == 0:
                raise core.BackupError("ZFS stream sender produced an empty stream")
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            process.stdout.close()
            process.stderr.close()
    return {"sha256": digest.hexdigest(), "size": size}


def available_base(config, source, chain):
    output = config.remote(
        ["zfs", "list", "-H", "-p", "-t", "snapshot", "-o", "name,guid", "-r", source.dataset]
    )
    rows = [line.split("\t") for line in output.splitlines() if line]
    if any(len(row) != 2 or not row[1].isdigit() for row in rows):
        raise core.BackupError("Invalid ZFS base snapshot response")
    base = chain[-1]
    return dict(rows).get(source.dataset + "@" + base["snapshot"]) == base["to_guid"]


def validate_streams(snapshot, manifest):
    """Validate paths and full/incremental dependencies before reuse or receive."""
    result = {}
    for source in manifest.get("raw_streams", []):
        name = source["name"]
        directory = hashlib.sha256(name.encode()).hexdigest()
        if source.get("directory") != directory or name in result:
            raise core.BackupError("Invalid raw stream directory")
        previous = None
        chain = source.get("streams", [])
        if not chain:
            raise core.BackupError("Missing raw stream chain")
        for index, item in enumerate(chain):
            guid = item.get("to_guid")
            if not isinstance(guid, str) or not guid.isdigit():
                raise core.BackupError("Invalid raw stream GUID")
            filename = f"{index:06d}-{guid}.zfs"
            if item.get("file") != filename or item.get("from_guid") != previous:
                raise core.BackupError("Invalid raw stream dependency chain")
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", item.get("snapshot", "")):
                raise core.BackupError("Invalid raw stream snapshot name")
            path = snapshot / "data" / directory / filename
            if path.is_symlink() or not path.is_file() or path.stat().st_size != item.get("size"):
                raise core.BackupError("Missing or invalid raw stream")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != item.get("sha256"):
                raise core.BackupError("Raw stream checksum mismatch")
            previous = guid
        result[name] = source
    expected = {s["name"]: s for s in manifest["sources"]}
    if set(result) != set(expected):
        raise core.BackupError("Raw stream source set mismatch")
    for name, source in result.items():
        if (
            source.get("dataset") != expected[name]["dataset"]
            or source["streams"][-1]["to_guid"] != expected[name]["guid"]
            or source.get("kind", "filesystem") not in ("filesystem", "volume")
            or source.get("kind", "filesystem") != expected[name].get("kind", "filesystem")
        ):
            raise core.BackupError("Raw stream source identity mismatch")
    return result


def backup_streams(config, sources, stage, entries):
    # Check even if a caller supplied a plan; never substitute a plaintext send.
    require_encryption(config, sources)
    previous_path = None
    previous = {}
    if entries:
        previous_path, previous_manifest = entries[-1]
        if previous_manifest.get("storage") != "zfs_raw":
            raise core.BackupError("Cannot mix plaintext and encrypted backups; use a new target")
        integrity.verify(previous_path, previous_manifest)
        previous = validate_streams(previous_path, previous_manifest)
    results = []
    for source in sources:
        directory = hashlib.sha256(source.name.encode()).hexdigest()
        destination = stage / "data" / directory
        destination.mkdir(parents=True, mode=0o700)
        old = previous.get(source.name)
        if old and (
            old["dataset"] != source.dataset or old.get("kind", "filesystem") != source.kind
        ):
            old = None
        chain = list(old["streams"]) if old else []
        unchanged = bool(chain and chain[-1]["to_guid"] == source.guid)
        if chain and not unchanged:
            if len(chain) >= config.raw_full_every or not available_base(config, source, chain):
                chain = []  # A new full stream removes the unavailable/overlong dependency.
        for item in chain:
            os.link(previous_path / "data" / directory / item["file"], destination / item["file"])
        if not unchanged:
            full_name = source.dataset + "@" + source.snapshot
            actual = config.remote(
                ["zfs", "get", "-H", "-p", "-o", "value", "guid", full_name]
            ).strip()
            if actual != source.guid:
                raise core.BackupError("Source snapshot changed after planning")
            send = [
                "timeout",
                "-k",
                "10",
                str(int(config.timeout_seconds)),
                "zfs",
                "send",
                "-w",
                "-p",
            ]
            if chain:
                send += ["-i", source.dataset + "@" + chain[-1]["snapshot"]]
            send.append(full_name)
            filename = f"{len(chain):06d}-{source.guid}.zfs"
            info = stream_to_file(
                config.remote_argv(send),
                destination / filename,
                timeout=config.timeout_seconds,
                io_timeout=config.io_timeout_seconds,
                bandwidth_limit_kib=config.bandwidth_limit_kib,
            )
            actual = config.remote(
                ["zfs", "get", "-H", "-p", "-o", "value", "guid", full_name]
            ).strip()
            if actual != source.guid:
                raise core.BackupError("Source snapshot changed during transfer")
            if chain and not available_base(config, source, chain):
                raise core.BackupError("Incremental base changed during transfer")
            chain.append(
                {
                    "file": filename,
                    "from_guid": chain[-1]["to_guid"] if chain else None,
                    "to_guid": source.guid,
                    "snapshot": source.snapshot,
                    **info,
                }
            )
        results.append(
            {
                "name": source.name,
                "dataset": source.dataset,
                "kind": source.kind,
                "directory": directory,
                "streams": chain,
            }
        )
    return results


def restore(config, snapshot, manifest, target, source_name):
    """Receive a selected source and its children into new, unmounted NAS datasets."""
    if manifest.get("storage") != "zfs_raw":
        raise core.BackupError("restore-zfs requires an encrypted raw backup")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", target)
        or "/" not in target
        or any(p in ("", ".", "..") for p in target.split("/"))
    ):
        raise core.BackupError("Restore target must be a new ZFS dataset below an existing pool")
    all_sources = validate_streams(snapshot, manifest)
    roots = [name for name in all_sources if "/" not in name]
    if source_name is None and len(roots) == 1:
        source_name = roots[0]
    if source_name not in all_sources:
        raise core.BackupError("Select a source with --source; see napback list")
    selected = sorted(
        (
            s
            for name, s in all_sources.items()
            if name == source_name or name.startswith(source_name + "/")
        ),
        key=lambda s: (s["name"].count("/"), s["name"]),
    )
    pool = target.split("/", 1)[0]
    existing = set(
        config.remote(
            ["zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", pool]
        ).splitlines()
    )
    if target in existing or any(name.startswith(target + "/") for name in existing):
        raise core.BackupError("Restore dataset already exists; nothing was overwritten")
    if target.rsplit("/", 1)[0] not in existing:
        raise core.BackupError("Restore target parent must already exist")
    received = []
    for source in selected:
        destination = target + source["name"][len(source_name) :]
        for index, item in enumerate(source["streams"]):
            receive = [
                "timeout",
                "-k",
                "10",
                str(int(config.timeout_seconds)),
                "zfs",
                "receive",
                "-u",
            ]
            if source.get("kind", "filesystem") == "volume":
                receive += ["-o", "volmode=none"]
            elif index == 0:
                receive += ["-o", "mountpoint=none", "-o", "canmount=noauto"]
            receive.append(destination)  # Never use -F, -R, rollback, or destroy.
            path = snapshot / "data" / source["directory"] / item["file"]
            with path.open("rb") as stream:
                core.command(
                    config.remote_argv(receive), stdin=stream, timeout=config.timeout_seconds
                )
            actual = config.remote(
                [
                    "zfs",
                    "get",
                    "-H",
                    "-p",
                    "-o",
                    "value",
                    "guid",
                    destination + "@" + item["snapshot"],
                ]
            ).strip()
            if actual != item["to_guid"]:
                raise core.BackupError("Received snapshot GUID mismatch")
        kind = config.remote(["zfs", "get", "-H", "-o", "value", "type", destination]).strip()
        if kind != source.get("kind", "filesystem"):
            raise core.BackupError("Received dataset type mismatch")
        encrypted = config.remote(
            ["zfs", "get", "-H", "-o", "value", "encryption", destination]
        ).strip()
        if encrypted == "off":
            raise core.BackupError("Received dataset unexpectedly lost encryption")
        received.append(destination)
    return {
        "status": "restored_zfs",
        "datasets": received,
        "note": "Filesystems remain unmounted; virtual disks remain hidden (volmode=none). Load original ZFS keys before enabling a restored filesystem or disk. No VM is created or started.",
    }
