"""Portable content inventory for offline verification and restore checks."""

import hashlib
import json
import os
import stat
from pathlib import Path

from .core import BackupError


def inventory(root):
    def visit(directory):
        for path in sorted(directory.iterdir()):
            info = path.lstat()
            entry = {"path": str(path.relative_to(root))}
            if stat.S_ISLNK(info.st_mode):
                entry.update(kind="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                entry.update(kind="directory")
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                entry.update(kind="file", size=info.st_size, sha256=digest.hexdigest())
            else:
                entry.update(kind="special", mode=info.st_mode, rdev=info.st_rdev)
            attrs = hashlib.sha256()
            for name in sorted(os.listxattr(path, follow_symlinks=False)):
                value = os.getxattr(path, name, follow_symlinks=False)
                attrs.update(len(name.encode()).to_bytes(8, "big") + name.encode())
                attrs.update(len(value).to_bytes(8, "big") + value)
            entry.update(xattrs_sha256=attrs.hexdigest(), mode=stat.S_IMODE(info.st_mode))
            yield entry
            if stat.S_ISDIR(info.st_mode):
                yield from visit(path)

    yield from visit(Path(root))


def record(stage):
    digest = hashlib.sha256()
    with (stage / "inventory.jsonl").open("xb") as stream:
        for entry in inventory(stage / "data"):
            line = (json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n").encode()
            stream.write(line)
            digest.update(line)
        stream.flush()
        os.fsync(stream.fileno())
    return digest.hexdigest()


def verify(snapshot, manifest):
    digest = hashlib.sha256()
    actual = iter(inventory(snapshot / "data"))
    count = 0
    with (snapshot / "inventory.jsonl").open("rb") as stream:
        for line in stream:
            digest.update(line)
            expected = json.loads(line)
            entry = next(actual, None)
            if entry != expected:
                raise BackupError(f"Integrity mismatch at {expected.get('path')}")
            count += 1
    if next(actual, None) is not None:
        raise BackupError("Unexpected extra entries in snapshot")
    if digest.hexdigest() != manifest.get("inventory_sha256"):
        raise BackupError("Inventory checksum mismatch")
    return count
