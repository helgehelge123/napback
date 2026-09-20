# Napback

**Pull encrypted ZFS snapshots when your Linux PC is awake.**

[Deutsche Anleitung](docs/kurzanleitung.md) · [Encryption](docs/encryption.md) · [Configuration](docs/configuration.md) · [Validation](docs/testing.md)

Napback checks for **new ZFS snapshots** while your Linux PC is awake. New
snapshot GUIDs trigger a backup without a 24-hour wait; unchanged generations
are skipped. Configure polling from 1 to 1440 minutes during setup or in the
system tray (default: one minute). After login or resume, the next eligible
check picks up the newest available generation.

New installations default to `zfs_raw` storage. Napback pulls native encrypted
ZFS send streams over SSH into ordinary files on the PC. **The original ZFS
encryption is preserved. No LUKS, local ZFS installation or local decryption key
is required.** All selected datasets, including children, must already be
ZFS-encrypted. Restoring requires a compatible ZFS system, such as TrueNAS, and
the original keys. Keep those keys separately from the NAS.

This is a prerelease. The backend uses OpenZFS for encryption, replication and
receive; Napback manages source selection, scheduling, archive generations,
checksums and retention. See the [validation record](docs/testing.md).

## Storage modes

| Mode | PC storage | Restore |
| --- | --- | --- |
| `zfs_raw` — setup default | Encrypted full/incremental `.zfs` streams in a normal folder; no local keys | Receive into new ZFS datasets, then unlock with original keys |
| `files` — explicit alternative | Unencrypted file versions with hard-linked unchanged files | Ordinary file restore on Linux |

`zfs_raw` rejects unencrypted sources. It does not create encryption for an
unencrypted NAS dataset. Existing 0.1/0.2 configurations retain their `files`
behavior; converting requires a **new target repository**, so old plaintext
cannot be mistaken for an encrypted archive. See [migration](docs/encryption.md).

## Features

- PC-initiated SSH pull with strict host-key checking; no inbound PC port or new NAS listener.
- Snapshot GUID detection, recursive children, common generation and freshness checks.
- Encrypted full and incremental ZFS streams, including backup of locked datasets.
- Shared base streams through hard links; retention preserves dependencies.
- Fresh full stream when a base snapshot disappears or a chain reaches its limit.
- Atomic publication after every source completes and local readback verifies.
- SHA-256 inventory, explicit verification, failed-transfer retry and one writer at a time.
- KDE-compatible system tray with status, check now, folder, interval, setup and logs.
- Python standard-library engine; optional PyQt6 tray. MIT licensed, no cloud account.

## Requirements

**PC:** Linux, Python 3.11+, OpenSSH client, systemd and a destination supporting
hard links and user xattrs, such as ext4, XFS or Btrfs. The initializer probes
these capabilities. FAT/exFAT are unsuitable. The optional tray needs a desktop
with StatusNotifierItem/system tray support. KDE Plasma is supported; GNOME may
need a tray extension.

**NAS for encrypted storage:** OpenZFS with native encryption and raw send,
`timeout`, and key-based SSH access to list and send the selected snapshots.
Passwordless `sudo -n` can be used where necessary. Docker and rsync are not
involved in raw transfers. All selected filesystem datasets must be encrypted.
Zvols are not supported.

**For `files` storage:** rsync 3.2+ on the PC and NAS, or the optional Docker sender
on the NAS. Source files must be unlocked and readable.

TrueNAS periodic snapshot tasks should cover selected children recursively.
The default snapshot prefix is `auto-`, with a maximum age of 48 hours. Napback
never creates, deletes or holds source snapshots. Database workloads may need
application-consistent dumps or coordinated snapshots; Napback does not pause apps.

## Install

Download and extract the source archive from
[Releases](https://github.com/helgehelge123/napback/releases), or use Git:

```sh
git clone https://github.com/helgehelge123/napback.git
cd napback
./install.sh
```

Run as your normal user. Missing Python/venv, rsync, SSH, systemd and Qt runtime
packages are installed through `pacman` (Arch/CachyOS), `apt-get` (Debian/Ubuntu)
or `dnf` (Fedora), using sudo only when needed. An old distribution Python must
be upgraded to 3.11+ first. Other distributions need their prerequisites installed
manually. No local ZFS packages are installed.

Napback and PyQt6 use `~/.local/share/napback/venv`; the launcher is
`~/.local/bin/napback`. Add `~/.local/bin` to PATH if needed. Existing installation
files are backed up beside the originals. Use `./install.sh --cli-only` without
the tray. The default installation starts the tray in a graphical session and
adds desktop autostart. Installation alone does not select sources or activate a
backup job; the initial tray state is “Not configured”.

## Configure and run

Start the guided setup (German by default, English available):

```sh
napback setup
napback setup --language en  # Use this instead for English
```

Each question includes a TrueNAS explanation and examples; `?` repeats the help.
“SSH host” means `user@NAS-address`, such as `backup@192.168.1.10`, or an existing
SSH alias. A separate field accepts the private SSH key's **path on the PC**;
the matching public key belongs in the TrueNAS user's settings. Setup explains
SSH access, passwordless sudo, dataset names, snapshot tasks and prefixes, local
storage, encryption and the minutes interval. Verify the NAS host key through a
trusted channel before accepting the first SSH connection.

Invalid field values can be corrected directly. Sources and existing snapshots
are validated before saving a configuration or creating a repository. Setup
then offers to enable the background timer. Accept `zfs_raw` to preserve
encryption. See [TrueNAS details](docs/truenas.md) and
[manual configuration](docs/configuration.md) for additional options.

```sh
napback plan           # Inspect selected snapshots; no transfer
napback run            # Check now and copy a new generation
napback status
napback list
napback verify         # Verify the local archive, without keys or NAS access
```

The independent systemd worker observes the minutes interval. “Check now” bypasses
that wait but still skips unchanged generations; `run --force` explicitly creates
another local version. Closing the tray leaves the worker running.

The timer runs while the user service manager is active. For execution before
login, optionally enable lingering with `loginctl enable-linger "$USER"`.
Calendar checks catch up after resume and service-manager restart. They do not
wake a sleeping PC. Polling can take another timer tick; long or failed transfers
retry at a subsequent eligible check. Only the newest common snapshot is selected,
not every intermediate generation created while offline.

```sh
systemctl --user status napback.timer napback.service
journalctl --user -u napback.service
systemctl --user disable --now napback.timer
```

## Restore encrypted datasets

```sh
napback verify
napback restore-zfs tank/recovered --source dataset-1
```

This restores the selected source and its children to **new datasets on the NAS
configured for this job**. The target's parent must exist; the target itself must
not exist. Napback never forces rollback or overwrites existing datasets.
Receives remain unmounted with `mountpoint=none` and `canmount=noauto`. Load the
original keys and choose safe mountpoints on the NAS to access files. Detailed
recovery, including another NAS, is in [encryption.md](docs/encryption.md).

The PC stores ciphertext, not browsable plaintext folders. Dataset names, snapshot
names, timestamps and sizes remain visible metadata. Losing every copy of the
original keys makes encrypted backups unrecoverable.

## Optional ordinary-file mode

Choose `files` explicitly for browsable, **unencrypted** local versions. It uses
rsync with fake-super metadata, checksum verification and hard links. It cannot
preserve ZFS encryption, dataset properties or complete native ACL semantics.
For setup and the optional short-lived read-only Docker sender, see
[TrueNAS details](docs/truenas.md).

```sh
napback restore /home/me/recovered  # For files-mode repositories only
```

Do not edit files within a backup: hard links can share contents across versions.
Use the restore command for correctly decoded symlinks. Privileged file metadata
recovery is documented in [recovery.md](docs/recovery.md).

## Retention and limits

Default retention is 30 completed generations; `keep: 0` keeps all. Older versions
are deleted only after a new complete version is durable. Every retained raw
version includes hard links to all its required base/incremental streams. Set
`raw_full_every` to control chain length (default: 30 streams). A new full stream
may require substantial free space; it never deletes old successful backups to
make room for an unfinished transfer.

Stream files are archives, not scrubbed ZFS replicas. Corruption is detected by
checksums, but one damaged base stream can affect all generations depending on
it; Napback cannot repair it. Maintain another independent backup. Interrupted
streams are retried, without byte-level resume. Source snapshots must outlive a
transfer. Custom mountpoints are supported by raw storage; files storage needs
conventional nested mountpoints or separate source entries.

Native ZFS receive retains filesystem contents and native metadata. The storage
pool layout, TrueNAS users/shares, application configuration outside the selected
datasets and independent encryption keys still require separate backups.

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tray]' pytest
python -m pytest -q
```

Automated tests use temporary synthetic data. Real encrypted NAS validation uses
dedicated test datasets and checks full/incremental receive, original-key unlock,
contents, metadata and locked-source backup. See [testing.md](docs/testing.md).
