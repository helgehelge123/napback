"""Synthetic NAS metadata for browser/API tests; no network or real user keys."""

import json
import time

from napback import core


def fake_remote(self, args):
    now = int(time.time())
    datasets = [
        ("Vault", "filesystem", "aes-256-gcm"),
        ("Vault/photos", "filesystem", "aes-256-gcm"),
        ("Vault/missing", "filesystem", "aes-256-gcm"),
        ("Vault/disk", "volume", "aes-256-gcm"),
        ("Other", "filesystem", "off"),
    ]
    if args[:2] == ["midclt", "call"]:
        return json.dumps(
            [
                {
                    "dataset": "Vault",
                    "recursive": True,
                    "exclude": ["Vault/missing"],
                    "enabled": True,
                    "naming_schema": "auto-%Y-%m-%d_%H-%M",
                    "schedule": {"hour": "0"},
                }
            ]
        )
    if args[:2] == ["zfs", "get"]:
        return "aes-256-gcm\n" if args[-1].startswith("Vault") else "off\n"
    column = args[args.index("-o") + 1]
    root = args[-1] if args[-1] in {row[0] for row in datasets} else None
    selected = [
        row for row in datasets if root is None or row[0] == root or row[0].startswith(root + "/")
    ]
    if column == "name,type,used,referenced,encryption,mountpoint":
        return "".join(
            f"{name}\t{kind}\t4096\t2048\t{enc}\t/nas/{name}\n" for name, kind, enc in selected
        )
    if column == "name,mountpoint":
        return "".join(
            f"{name}\t/nas/{name}\n" for name, kind, _ in selected if kind == "filesystem"
        )
    if column == "name,creation,guid":
        return "".join(
            f"{name}@auto-{i}\t{now - (3 - i) * 3600}\t{index * 100 + i}\n"
            for index, (name, _, _) in enumerate(selected, 1)
            if not name.endswith("missing")
            for i in [1, 2]
        )
    raise core.BackupError("Unexpected fake NAS command")
