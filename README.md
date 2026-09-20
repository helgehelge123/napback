# Napback

**Pull versioned NAS backups when your Linux PC is awake.**

[Deutsche Kurzanleitung](docs/kurzanleitung.md) · [Configuration](docs/configuration.md) · [Validation](docs/testing.md)

Napback checks for **new ZFS snapshots** while your Linux PC is awake. A new
snapshot starts a backup without waiting 24 hours; unchanged snapshots are
skipped. The polling interval is configurable in minutes during setup and from
the system tray (default: one minute). After login, boot with user lingering
enabled, or resume, the next check picks up the newest available snapshot.
Failed transfers are retried. Your PC does not need to stay on overnight.

On TrueNAS, Napback reads existing ZFS snapshots over SSH. An optional, short-lived
Docker container supplies rsync on the NAS. The destination can be an ordinary
Linux filesystem; it does **not** need ZFS. Each backup is a browsable directory.
Unchanged files share disk space through hard links.

This is an early release, not a replacement for your only backup. The transfer
engine is rsync; Napback adds scheduling, source selection, atomic publication,
integrity inventories and recovery checks. See [validation](docs/testing.md).

## Features

- Pull from the PC; no inbound PC port and no new NAS network listener.
- New-snapshot detection using ZFS GUIDs, including replacements with the same name.
- Configurable polling from 1 to 1440 minutes; no duplicate backup for an unchanged snapshot.
- System tray status and actions on KDE Plasma and other compatible desktops.
- Optional rolling interval mode for ordinary directory sources (default: 24 hours).
- Automatic retry after missed schedules, disconnected storage, or network failure.
- Recursive dataset discovery and the newest common ZFS snapshot across children.
- Snapshot freshness checks; never silently fall back to the live dataset.
- Full directory views with hard-linked unchanged files, configurable retention.
- Transfer verification plus a SHA-256 inventory for offline verification.
- One backup at a time, repository identity and mountpoint checks.
- Publish only after every source completes; interrupted backups stay incomplete.
- User-mode operation on the PC. Privileged source metadata is stored with rsync
  `--fake-super` in extended attributes.
- Interactive setup, status, restore and systemd installation commands.
- Python standard-library backup engine, optional PyQt6 tray. MIT licensed.
  No account or cloud service.

## Requirements

**PC:** Linux, Python 3.11+, rsync 3.2+, OpenSSH client, systemd, and a destination
with working hard links and user extended attributes, such as ext4, XFS or Btrfs.
`napback init` probes both. FAT/exFAT and generic network shares are unsuitable.
The tray additionally requires PyQt6 and a desktop with a StatusNotifierItem or
system tray host. KDE Plasma is supported; GNOME may need a tray extension.

**NAS:** working key-based SSH with a verified host key. For ZFS sources, `zfs`
and access to the requested snapshots. For direct transfers, rsync; alternatively
Docker and the source image. Existing TrueNAS periodic snapshot tasks should run
at least daily and cover all selected children. The default snapshot-name prefix
is `auto-`; the newest common snapshot may be at most 48 hours old.

A ZFS filesystem snapshot is crash-consistent. Database applications may need
separate, application-consistent dumps or coordinated snapshot preparation.
Napback does not pause apps or perform database dumps.

## Install

Download and extract the source archive from
[Releases](https://github.com/helgehelge123/napback/releases), or clone using Git:

```sh
git clone https://github.com/helgehelge123/napback.git
cd napback
./install.sh
```

The installer creates an isolated environment under `~/.local/share/napback` and
an executable at `~/.local/bin/napback`. Existing installation files are backed
up beside the originals. Ensure `~/.local/bin` is in your PATH.

Missing Python/venv, rsync, OpenSSH, systemd and Qt runtime packages are installed
automatically using `pacman` (Arch/CachyOS), `apt-get` (Debian/Ubuntu), or `dnf`
(Fedora). Run the installer as your normal user; it uses `sudo` only for missing
system packages and may ask for your password. Python 3.11+ is required; older
distribution Python versions must be upgraded first. PyQt6 is installed in the
private environment. The tray starts immediately in a graphical session and
automatically at subsequent desktop logins. Without a configuration it shows
“Not configured”; installation alone does not activate a backup job.

Use `./install.sh --cli-only` on machines where you do not want the tray or its
GUI dependencies. On unsupported distributions, install the requirements with
your package manager before running the installer.

Alternatively, with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install '.[tray]'
napback install-tray
```

Before setup, configure an SSH alias and verify the NAS host key through a trusted
channel. Confirm that this command succeeds without a password prompt:

```sh
ssh -oBatchMode=yes my-nas 'zfs list'
# If your chosen NAS account requires sudo:
ssh -oBatchMode=yes my-nas 'sudo -n zfs list'
```

Napback always uses strict host-key checking and noninteractive SSH/sudo. It does
not automatically trust a new host key or store passwords.

## Configure and run

```sh
napback setup
```

Setup lists ZFS filesystems, asks for datasets, a destination and a polling
interval in minutes, validates the available snapshots and offers to enable the
background timer. Child datasets are included by default. The target must be
new or empty. For explicit source
names, custom intervals or non-ZFS paths, use [manual configuration](docs/configuration.md).

```sh
napback plan          # Show exactly which snapshots would be read
napback run           # Check now; copy only a new snapshot (or a due interval job)
napback run --force   # Explicitly allow another copy of the same snapshot
napback status
napback list
napback verify        # Read and check the latest local backup without NAS access
```

The user timer runs while your user service manager is active. To run before
login, enable lingering once (your distribution may ask for authorization):

```sh
loginctl enable-linger "$USER"
```

The minute timer observes the configured polling interval before contacting the
NAS. It does not create a backup every minute and does not wake a sleeping PC.
Calendar timers catch up after resume; `Persistent=true` catches up when the user
service manager restarts. Polling is approximate, with up to another timer tick
of delay. A backup in progress may fail after a long suspend; the next check
retries it. Only the newest common snapshot is selected, not every intermediate
snapshot created while the PC was offline.

The tray shows checks, transfers, success and errors. Its menu provides “Check
now”, the backup folder, configuration, setup, logs and a minutes interval dialog.
Closing the tray leaves the independent background worker running. The tray
itself does not enable a disabled backup timer.

```sh
systemctl --user status napback.timer napback.service
journalctl --user -u napback.service
systemctl --user disable --now napback.timer  # Disable future automatic runs
```

## Optional Docker sender on TrueNAS

Copy this repository to a persistent NAS directory, then build once:

```sh
sudo docker compose -f docker/compose.yaml build
```

Set `"docker_image": "napback-source:0.1.0"` in the PC configuration. Napback
starts a container for each transfer/verification through the existing SSH
connection. It binds only the selected snapshot read-only, with no network,
no Docker socket, and no privileged-container mode. The container is removed
when the rsync process exits. There is no permanent daemon or scheduled job on
the NAS to configure. [TrueNAS details](docs/truenas.md)

Docker access and unrestricted sudo are administrator privileges. The isolated
sender protects against accidental writes by the transfer process; it does **not**
turn an administrator SSH key into a read-only key. See [security](SECURITY.md).

## Browse and restore

**Encryption:** SSH encrypts transport. Native ZFS encryption is **not inherited**:
the NAS must expose unlocked, readable snapshot files, and Napback writes ordinary
files on the PC. For encryption at rest, choose an already encrypted destination
filesystem, for example a mounted LUKS volume. Napback does not create, unlock or
format encrypted volumes. No native ZFS dataset or encrypted ZFS send stream is
stored on the PC.

```text
backup-directory/
  latest -> snapshots/20260920T120000Z-…/data
  snapshots/
    20260920T120000Z-…/
      manifest.json
      inventory.jsonl
      data/
        documents/
        photos/
  work/                      # Incomplete or interrupted attempts
```

Copy files out; do not edit files inside a backup. Hard links mean an in-place
edit could change the same file in several versions. Depending on rsync version,
symlinks and special files may be encoded as ordinary files plus extended
attributes. Use `restore` to decode symlinks correctly.

```sh
napback restore /home/me/recovered
napback restore /home/me/older-files --snapshot 20260920T120000Z-012345abcdef
```

Restore verifies the saved inventory first and refuses a nonempty destination.
The normal user restore returns file contents, directory structure, symlinks,
hard links, modification times and user xattrs. It deliberately does not recreate
privileged ownership, ACLs or device nodes. For full privileged restoration,
see [recovery](docs/recovery.md).

Default retention is the newest 30 successful backups. Older backups are deleted
only after a new backup is complete and durable. Set `keep` to `0` to disable
automatic deletion. Failed transfers never rotate completed backups away.

## Scope and limitations

- Filesystem-level backups, not `zfs send` streams: ZFS properties, encryption
  keys, zvols, pool topology and native snapshot history are not replicated.
- Recursive children must have conventional nested mountpoints; custom child
  mountpoints require separate source entries.
- Source snapshots are not held or modified. If NAS retention removes one during
  transfer, the run fails and is retried. Keep them longer than a full transfer.
- A common snapshot name identifies a generation across children. Atomic
  cross-dataset consistency requires a recursive snapshot created together on
  the NAS. Separate configured sources can select different generations.
- POSIX metadata is handled by rsync. TrueNAS NFSv4 ACLs are not equivalent to
  POSIX ACLs; do not treat this as a complete NFSv4 ACL or SMB configuration backup.
- A plain `path` source is a live directory unless you point it at an immutable
  snapshot. Verification can detect some concurrent changes, not prove an atomic
  application-level state.
- Interrupted work is discarded safely and retried from the last completed
  backup. Byte-level resumption of a partially copied large file is not provided.
- Checksums read source and destination data. Large datasets can take substantial
  time even when little changed. Hard links deduplicate whole unchanged files,
  not blocks inside modified files. Sparse files are transferred sparsely.
- Backups are not encrypted at rest by Napback. Use an encrypted destination
  filesystem if required. SSH encrypts transport.
- The tray is a small status/menu interface; dataset selection remains a terminal
  setup assistant.

## Existing projects

[rsnapshot](https://rsnapshot.org/) already provides pull backups over SSH and
hard-link versions. [Back In Time](https://github.com/bit-team/backintime) provides
a desktop interface and repeated scheduling based on the last successful backup.
[restic](https://restic.net/) provides encrypted, deduplicated repositories.
These are established choices worth considering. Napback focuses on one workflow:
a sometimes-offline Linux PC pulling existing TrueNAS snapshots into ordinary
local directories. See [research notes](docs/research.md).

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tray]' pytest
python -m pytest -q
```

Tests use isolated temporary directories. Real NAS testing is documented
separately and must use dedicated synthetic datasets.
