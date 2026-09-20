"""Read-only TrueNAS VM settings export; runs standalone over SSH.

Disk contents are separate ZFS streams. Firmware and settings are captured now,
not at the older disk snapshot time. No VM is paused, stopped or started.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

MAX_EXPORT = 64 * 1024 * 1024
MAX_FIRMWARE = 32 * 1024 * 1024


def command(argv):
    result = subprocess.run(argv, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError("VM metadata command failed")
    if len(result.stdout) > MAX_EXPORT:
        raise ValueError("VM metadata exceeds limit")
    return result.stdout


def definition_signature(vms):
    return [{k: v for k, v in vm.items() if k != "status"} for vm in vms]


def read_firmware(root):
    result, total = [], 0
    if not root.exists():
        return result

    def unreadable(error):
        raise error

    for directory, children, files in os.walk(root, onerror=unreadable, followlinks=False):
        for name in children:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Firmware directory contains a symlink")
        for name in sorted(files):
            path = Path(directory) / name
            before = path.lstat()
            total += before.st_size
            if not stat.S_ISREG(before.st_mode) or total > MAX_FIRMWARE:
                raise ValueError("Unsafe or oversized firmware file")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                content = stream.read(MAX_FIRMWARE + 1)
                after = os.fstat(stream.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or len(content) != before.st_size:
                raise ValueError("Firmware changed during export")
            result.append(
                {
                    "path": str(path.relative_to(root)),
                    "size": len(content),
                    "mode": stat.S_IMODE(before.st_mode),
                    "uid": before.st_uid,
                    "gid": before.st_gid,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "base64": base64.b64encode(content).decode("ascii"),
                }
            )
    return sorted(result, key=lambda f: f["path"])


def export(root=Path("/var/db/system/vm"), run=command):
    vms = json.loads(run(["midclt", "call", "vm.query"]))
    if not isinstance(vms, list) or any(not isinstance(v, dict) or "devices" not in v for v in vms):
        raise ValueError("Invalid VM query response")
    # An active emulated TPM can change independently of the disk snapshot.
    if any(
        v.get("trusted_platform_module") and v.get("status", {}).get("state") != "STOPPED"
        for v in vms
    ):
        raise ValueError("Active TPM state requires an offline backup")
    firmware = read_firmware(root)
    for vm in vms:
        if vm.get("trusted_platform_module"):
            prefix = f"tpm/{vm['id']}_{vm['name']}_tpm_state/"
            if not any(f["path"].startswith(prefix) for f in firmware):
                raise ValueError("Missing TPM state")
        if vm.get("bootloader") == "UEFI":
            name = f"nvram/{vm['id']}_{vm['name']}_VARS.fd"
            if not any(f["path"] == name for f in firmware):
                raise ValueError("Missing UEFI variable store")
    if definition_signature(vms) != definition_signature(
        json.loads(run(["midclt", "call", "vm.query"]))
    ):
        raise ValueError("VM settings changed during export")
    if read_firmware(root) != firmware:
        raise ValueError("Firmware changed during export")
    result = json.dumps(
        {
            "format": 1,
            "exported_at": time.time(),
            "truenas_version": run(["midclt", "call", "system.version"])
            .decode()
            .strip()
            .strip('"'),
            "firmware_root": str(root),
            "vms": vms,
            "firmware": firmware,
            "note": "Settings and firmware at export time. Disk contents, RAM, passthrough hardware and legacy Instances are not included. Restore disk snapshots separately; no application consistency is implied.",
        }
    ).encode()
    if len(result) > MAX_EXPORT:
        raise ValueError("VM export exceeds limit")
    return result


if __name__ == "__main__":
    try:
        sys.stdout.buffer.write(export())
    except Exception:
        sys.stderr.write(
            "TrueNAS VM export failed. Check permissions, firmware files, active TPMs and concurrent VM changes.\n"
        )
        sys.exit(1)
