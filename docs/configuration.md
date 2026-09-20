# Configuration

Default file: `~/.config/napback/config.json`, or `$XDG_CONFIG_HOME/napback/config.json`.
Use `napback --config /absolute/path/config.json COMMAND` for another job.
The installer creates one timer for one configuration; multiple independent jobs
can use separately named units generated from the supplied unit structure.

## Manual setup

```sh
napback init /mnt/backup-disk/nas
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
  "host": "my-nas",
  "sudo": true,
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
| `interval_hours` | `24` | Hours since last successful completion, minimum 1 |
| `keep` | `30` | Successful versions to retain; `0` keeps all |
| `min_free_bytes` | `1073741824` | Free-space threshold before starting; not a size reservation |
| `verify` | `true` | Compare transferred files to the source using rsync checksums before publication |
| `host` | unset | SSH alias or `user@hostname`; unset means local path sources |
| `ssh_options` | `[]` | Extra SSH arguments, for example `["-i", "/home/me/.ssh/backup"]` |
| `sudo` | `false` | Prepend `sudo -n` to NAS commands |
| `docker_image` | unset | Use a temporary source container instead of NAS-installed rsync |
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
recent version exists. Retention, interval, bandwidth and free-space changes do
not by themselves force another transfer. A clock more than five minutes behind
the previous success makes a new backup due instead of postponing it indefinitely.

`verify: false` disables the second source comparison only. File selection still
uses checksums, and each backup still gets a SHA-256 inventory of local contents.
The inventory is not a replacement for an application-consistent source snapshot.

## Ordinary directory sources

For a local directory, omit `host`:

```json
{
  "target": "/mnt/backup-disk/files",
  "repository_id": "PASTE_THE_ID_FROM_INIT",
  "mountpoint": "/mnt/backup-disk",
  "sources": [{"name": "documents", "path": "/home/me/Documents"}]
}
```

Add `host` to read a directory on the NAS. A `path` source does not select ZFS
snapshots automatically. Use an immutable snapshot path for a stable source.
Local sources and their destination must not overlap. `--one-file-system` avoids
silently including mounted filesystems under a source; configure those separately.

## Background behavior

The service is `Type=oneshot`; no Python daemon polls continuously. The user timer
has `OnStartupSec=30s`, a minute calendar schedule, and `Persistent=true`.
Napback reads completed manifests to decide whether a backup is due. A timer
activation, failed attempt or network connection is never considered a success.

On a PC with no user lingering, the user manager starts at login. To run before
login as well, enable lingering using `loginctl enable-linger USER`. The timer
does not wake the PC. Missed calendar events are handled on resume; an unavailable
network is retried on the next minute. Inspect errors in the user journal and
with `napback status`.
