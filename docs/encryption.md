# Native ZFS encryption without LUKS

Version 0.3 adds `storage: "zfs_raw"`, the default in interactive setup and
`napback init`. A normal folder on ext4, XFS or Btrfs can store encrypted backups.
The PC does not need a ZFS pool, LUKS volume, password file or decryption key.

## What is preserved

Napback invokes `zfs send -w -p` on the NAS. OpenZFS sends the encrypted on-disk
blocks and their encryption metadata; the corresponding original ZFS key unlocks
the dataset after receive. The source key does not have to be loaded while backing
up. See [OpenZFS raw send](https://openzfs.github.io/openzfs-docs/man/v2.4/8/zfs-send.8.html).

All selected datasets and children must already use native ZFS encryption.
Since 0.7 this includes virtual disks (ZFS volumes/zvols), as well as filesystems.
Napback checks this and rejects unencrypted datasets; it never substitutes an
unencrypted send or silently changes the NAS. Docker is not used for raw sends.
SSH additionally protects transport.

File contents and filenames inside the dataset stay encrypted. Archive manifests
and ZFS stream headers expose dataset/snapshot names, GUIDs, sizes, timestamps
and some properties. This is not metadata-hiding encryption. No unwrapped key or
plaintext staging copy is written to the PC by this backend.

Keep the original ZFS keys/passphrases in a separate, recoverable location. Napback
does not export them. Losing the NAS plus its only key copy makes recovery
impossible. Keys for older generations may also be needed after key changes;
key-rotation scenarios are not covered by the current real-world tests.

## Files on the PC

```text
backup-directory/
  .napback-repository.json
  latest -> snapshots/GENERATION/data
  snapshots/GENERATION/
    manifest.json
    inventory.jsonl
    data/SOURCE-ID/
      000000-GUID.zfs      # Encrypted full stream
      000001-GUID.zfs      # Encrypted incremental stream
  work/                   # Unpublished encrypted attempts
```

A new generation normally adds only an incremental stream. If the previous
snapshot is gone or has a different GUID, Napback sends a fresh full stream.
It also starts fresh when a chain reaches `raw_full_every` (default 30 streams).
Shared streams use hard links. Each retained generation contains its complete
chain; pruning old generation directories cannot remove a remaining generation's
required base. Do not edit the stream files or move individual files between
repositories. Copying the whole repository preserves recovery, though ordinary
copy tools may duplicate hard-linked storage.

SHA-256 is computed while receiving, checked by local readback before publication,
and recorded in the inventory. `napback verify` reads the archive and validates
stream dependencies without requiring the NAS or keys. Checksums detect accidental
corruption, not a malicious rewrite of both data and manifests. A ZFS receive
also validates the stream's native checksums. Stream archives have no redundant
pool blocks for self-repair; retain an independent backup.

## Configure

```sh
napback init /mnt/backup-disk/encrypted-nas --storage zfs_raw
```

Use the returned identity and mountpoint in the configuration:

```json
{
  "target": "/mnt/backup-disk/encrypted-nas",
  "repository_id": "PASTE_THE_ID_FROM_INIT",
  "mountpoint": "/mnt/backup-disk",
  "storage": "zfs_raw",
  "raw_full_every": 30,
  "host": "my-nas",
  "sudo": true,
  "trigger": "new_snapshot",
  "check_interval_minutes": 1,
  "sources": [{"name": "documents", "dataset": "tank/documents", "recursive": true}]
}
```

The NAS account needs ZFS list/get/send rights. `timeout` must be available on
the NAS; TrueNAS SCALE provides it. `verify: false` is rejected for raw storage.
The existing minute polling, tray and retention settings apply unchanged.

## Restore

```sh
napback verify
napback restore-zfs tank/recovered --source documents
```

The command sends the archived full stream and each incremental in order to
`zfs receive` on the configured NAS. It restores `documents` and its children
under `tank/recovered`. With one root source, `--source` is optional. Use
`--snapshot GENERATION` for an older local generation; `napback list` shows IDs.

The target's parent dataset must exist. The target and its children must be new.
Existing datasets are rejected. No `-F`, rollback, dataset deletion or forced
unmount is used. A failed receive can leave newly created incomplete datasets;
Napback leaves them in place for inspection. Retry into a new target after
resolving the cause, or explicitly manage the failed target yourself.

The restored datasets remain unmounted, with `mountpoint=none` and
`canmount=noauto`. Load the original keys on the NAS, select safe mountpoints,
and mount explicitly. Each received child can be a separate encryption root
requiring the same original key, even if it inherited that key on the source.
On TrueNAS, use its dataset/key controls or check the pool's `altroot` before
setting mountpoint properties: a pool imported with `/mnt` as altroot prefixes
that path automatically. Do not blindly prepend `/mnt` twice.

For a replacement NAS, copy the job configuration locally and change its `host`
and `ssh_options` to the replacement's verified SSH access, keeping the local
repository identity and target unchanged. Run `--config recovery.json verify`
and `--config recovery.json restore-zfs NEW_DATASET`. The original NAS does not
need to exist. The receiving ZFS version/pool must support the stream's features.

A PC without ZFS cannot directly decrypt or browse these streams. Restore them
on TrueNAS (or another compatible ZFS host) and copy out individual files there.
Native receive restores dataset file metadata, including native ACL data; pool
configuration, TrueNAS shares/users and external application settings are separate.

## Existing plaintext backups

Changing a JSON field cannot encrypt old directories. Existing 0.1/0.2 jobs remain
in `files` mode. Create a new raw repository and rerun setup with `zfs_raw`, or
update the job using the new repository's values. Different repository markers
prevent accidentally mixing raw and files storage. Napback does not delete or
rewrite the old plaintext backups. If you want them removed later, handle that
separately; moving/deleting files is not guaranteed secure erasure on SSDs.

For virtual disks, `restore-zfs` sets `volmode=none` instead of filesystem mount properties. The restored block device stays hidden until explicitly enabled. See [VM recovery](virtual-machines.md).
