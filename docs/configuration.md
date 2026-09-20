# Configuration

Default file: `~/.config/napback/config.json`, or `$XDG_CONFIG_HOME/napback/config.json`.
Use `napback --config /absolute/path/config.json COMMAND` for another job.
The installer creates one timer for one configuration; multiple independent jobs
can use separately named units generated from the supplied unit structure.

## Manual setup

```sh
napback init /mnt/backup-disk/nas --storage zfs_raw
```

This prints `target`, `repository_id`, and the detected `mountpoint`. Copy these
three values into a JSON configuration, then add your sources. Do not invent the
repository ID or run `init` over an existing backup. The initialization probe
requires hard links and user extended attributes. It does not format a disk.

Minimal ZFS configuration:

```json
{
  "target": "/mnt/backup-disk/nas",
  "repository_id": "PASTE_THE_ID_FROM_INIT",
  "mountpoint": "/mnt/backup-disk",
  "storage": "zfs_raw",
  "host": "my-nas",
  "sudo": true,
  "trigger": "new_snapshot",
  "check_interval_minutes": 1,
  "sources": [
    {"name": "documents", "dataset": "tank/documents", "recursive": true}
  ]
}
```

```sh
napback plan
napback run
napback verify
napback install-timer
```

The mountpoint is checked before the backup. A missing mount never causes Napback
to initialize another repository on the root disk. A marker additionally checks
the expected repository identity. Explicit mountpoints must match the filesystem
that actually contains the target. Repository paths must not contain symlinks.

## Options

| Key | Default | Meaning |
| --- | --- | --- |
| `target` | required | Absolute local repository path |
| `repository_id` | required | UUID returned by `init` |
| `mountpoint` | required | Expected filesystem mountpoint returned by `init` |
| `sources` | required | One or more named path or dataset sources |
| `storage` | `"files"` for old configs; `"zfs_raw"` in new setup/init | Native encrypted ZFS stream archive or ordinary unencrypted files; use a new repository to change modes |
| `raw_full_every` | `30` | Maximum raw stream chain length (1–1000); the next changed generation starts with a fresh full stream |
| `trigger` | `"auto"` | `new_snapshot` copies changed ZFS snapshot GUIDs; `interval` uses elapsed time; `auto` chooses snapshots when every source is a dataset, otherwise interval |
| `check_interval_minutes` | `1` | Polling interval in whole minutes, from 1 to 1440 |
| `interval_hours` | `24` | Only in interval mode: hours since last successful completion, minimum 1 |
| `keep` | `30` | Successful versions to retain; `0` keeps all |
| `min_free_bytes` | `1073741824` | Free-space threshold before starting; not a size reservation |
| `verify` | `true` | Files: rsync source comparison; raw: mandatory local ciphertext readback before publication |
| `host` | unset | SSH alias or `user@hostname`; unset means local path sources |
| `ssh_options` | `[]` | Extra SSH arguments, for example `["-i", "/home/me/.ssh/backup"]` |
| `sudo` | `false` | Prepend `sudo -n` to NAS commands |
| `docker_image` | unset | Files mode only: temporary source container instead of NAS-installed rsync; ignored by raw storage |
| `snapshot_prefix` | `"auto-"` | Select only snapshot names starting with this prefix; `""` allows any |
| `snapshot_max_age_hours` | `48` | Maximum age of the oldest selected child snapshot |
| `timeout_seconds` | `86400` | Maximum duration of each transfer or verification; also Docker sender lifetime |
| `io_timeout_seconds` | `120` | rsync inactivity timeout on both transfer ends |
| `bandwidth_limit_kib` | `0` | Transfer limit in KiB/s; `0` is unlimited |

Source names use letters, digits, dot, underscore and dash, starting with a
letter or digit. They become directory names under `data/`. Source path entries
accept `name` and `path`. ZFS entries accept `name`, `dataset` and `recursive`
(default true). Unknown options are rejected to catch misspellings.

Changing source-related configuration makes a backup due immediately, even if a
recent version exists. Retention, polling, trigger mode, interval, bandwidth and
free-space changes do not by themselves force another transfer. In interval
mode, a clock more than five minutes behind the previous success makes a new
backup due instead of postponing it indefinitely.

In snapshot mode the latest common snapshot name and each dataset's GUID are
compared with the last successful backup for this configuration. A change in any
selected dataset triggers a complete new version; unchanged files still share
space through hard links. An unchanged generation creates no new local version,
even after 24 hours. Stale or missing source snapshots remain errors; a failed
check never changes the last successful backup.

`new_snapshot` requires dataset sources exclusively. Mixed dataset/path jobs use
interval mode under `auto`, because plain paths have no snapshot GUID to watch.
To retain version 0.1's daily behavior, explicitly set `"trigger": "interval"`.
Otherwise old ZFS-only configurations automatically use the new behavior.
Version 0.1 manifests lack GUIDs, so upgrading copies the selected generation
once to establish the new baseline. Older local versions remain valid.

`verify: false` disables the second source comparison only. File selection still
uses checksums, and each backup still gets a SHA-256 inventory of local contents.
The inventory is not a replacement for an application-consistent source snapshot.
This option applies only to `files` mode; raw storage rejects disabled verification.

`zfs_raw` requires encrypted dataset sources, including every selected child.
It permits locked/unmounted datasets and custom mountpoints. ZFS and the original
keys are needed for restore, not for storage on the PC. See [encryption](encryption.md)
for key handling, incremental dependencies, migration and `restore-zfs`.

## Ordinary directory sources

For a local directory, omit `host`:

Initialize it explicitly with `napback init /mnt/backup-disk/files --storage files`.

```json
{
  "target": "/mnt/backup-disk/files",
  "repository_id": "PASTE_THE_ID_FROM_INIT",
  "mountpoint": "/mnt/backup-disk",
  "storage": "files",
  "sources": [{"name": "documents", "path": "/home/me/Documents"}]
}
```

Add `host` to read a directory on the NAS. A `path` source does not select ZFS
snapshots automatically. Use an immutable snapshot path for a stable source.
Local sources and their destination must not overlap. `--one-file-system` avoids
silently including mounted filesystems under a source; configure those separately.

## Background behavior

The backup service is `Type=oneshot`. The user timer has `OnStartupSec=30s`, a
minute calendar schedule, and `Persistent=true`. It calls `run --scheduled`,
which contacts the NAS only after `check_interval_minutes` since the previous
check started. Long backups never overlap; failed attempts retry on a subsequent
eligible tick. Rounding to timer ticks can add approximately another minute.
Changing the interval in JSON or the tray takes effect without reinstalling the
timer. `napback run` and the tray's “Check now” bypass the polling wait but still
skip unchanged snapshots; only `--force` overrides that safeguard.

Completed manifests determine the successful source generation. `last-check.json`
records check/transfer state and errors for `napback status` and the tray. A timer
activation, failed attempt or network connection is never considered a success.
In snapshot mode `status.due` is `null`: status reads local state without making
another NAS connection.

On a PC with no user lingering, the user manager starts at login. To run before
login as well, enable lingering using `loginctl enable-linger USER`. The timer
does not wake the PC. Missed calendar events are handled on resume; an unavailable
network is retried at the next eligible check. Inspect errors in the user journal,
the tray and with `napback status`.

The optional `napback-tray.service` starts through desktop autostart. It reads
local status every two seconds. Manual checks launch an independent transient
user service so quitting the tray does not terminate a backup. Reinstalling or
quitting the tray never stops the backup worker. Use `napback install-tray` to
register the tray, or `napback --config /path/job.json install-tray` for a custom
configuration (one installed tray entry per desktop user).
