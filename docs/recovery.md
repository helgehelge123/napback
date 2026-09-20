# Recovery and verification

## Normal user restore

```sh
napback verify
napback restore /home/me/recovered
```

`verify` reads the entire selected local version and checks its SHA-256 inventory,
file sizes, local modes, symlink representations and extended attributes. It
also checks that the inventory itself matches the digest in `manifest.json`.
It needs no NAS connection. A checksum mismatch aborts restore.

`restore` accepts only a new or empty destination outside the repository. It
decodes rsync fake-super symlinks, preserves regular file contents, modification
times, user xattrs and hard links, and checks the result with a checksum dry run.
It skips special files such as FIFOs and device nodes. It does not restore
privileged ownership or ACLs through the ordinary user command.

Never modify files directly under `latest` or `snapshots`. Multiple versions can
share an inode. Copy a file out before editing it.

## Full privileged file restore

For original uid/gid, mode, POSIX ACLs and special files, first verify the selected
snapshot, then use rsync 3.5 or newer as root with fake-super enabled **on the source only**:

```sh
napback verify SNAPSHOT_ID
sudo rsync -aHAX --numeric-ids --fake-super -M--super -- \
  /mnt/backup-disk/nas/snapshots/SNAPSHOT_ID/data/ /mnt/new-empty-recovery-target/
```

For a host with an older rsync, use the tested source image (rsync 3.5) for the
restore as well. The destination must already exist and be empty:

```sh
sudo docker run --rm --network none \
  --mount type=bind,src=/mnt/backup-disk/nas/snapshots/SNAPSHOT_ID/data,dst=/input,readonly \
  --mount type=bind,src=/mnt/new-empty-recovery-target,dst=/output \
  napback-source:0.1.0 \
  rsync -aHAX --numeric-ids --fake-super -M--super -- /input/ /output/
```

Older rsync builds tested on TrueNAS and Ubuntu mishandled local `-M` options.
Napback's normal backup and restore commands avoid this issue by running the
local sender/receiver through a small process transport, without a network socket.
Do not use the manual local `-M` command above with those older versions.

Use an empty recovery target and inspect it before replacing any production data.
This procedure restores filesystem metadata supported by rsync, not ZFS dataset
properties, NFSv4 ACL semantics or application configuration external to the
selected sources. Never point a restore command at the backup source itself.

## Interrupted backups

Incomplete attempts live under `work/` and never count as success. The next due
run removes only directories with a matching Napback owner marker, then starts
fresh while reusing unchanged files from the last completed version. It does not
reuse partial files from a failed attempt, avoiding accidental changes to older
hard links.

A completed manifest is the authoritative success record. If the process dies
after publication but before updating `latest`, the next run repairs that link.
Retention first moves a validated old version into the owned work area, so a
crash halfway through deletion cannot leave a damaged published version.

An unrecognized file, missing manifest or mismatched repository ID causes an
error requiring inspection. Napback will not delete unrelated files to repair
an unexpected repository layout. Do not fabricate markers to bypass this check.

## Lost PC configuration

The repository marker `.napback-repository.json` contains its identity. Recreate
a configuration with that identity, the correct target and mounted filesystem,
then the desired sources. **Do not run `init` over the repository.** Its
`manifest.json` files also record the selected source datasets and snapshot names.
Private SSH keys are intentionally not copied into backups by Napback itself.

## Corruption

Stop automatic backups while investigating a reported integrity mismatch:

```sh
systemctl --user disable --now napback.timer
```

Verify other versions, consider whether they share the same affected inode, and
recover from a verified independent copy. SHA-256 inventories detect accidental
corruption but are not signed; an attacker with repository write access can
rewrite both the data and inventory. Keep an independent or offline copy for
protection against that scenario.
