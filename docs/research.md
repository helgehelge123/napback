# Research and design choice

Reviewed on 2026-09-20. The requirement is a NAS-to-PC pull, completed at most once
per rolling interval, with catch-up when the PC becomes available.

| Project | What already fits | Integration needed for this workflow |
| --- | --- | --- |
| [rsnapshot](https://rsnapshot.org/rsnapshot/docs/docbook/rest.html) | SSH pull, hard-link directory versions | Last-success interval gate, explicit current ZFS snapshot discovery, workstation scheduling |
| [Back In Time](https://github.com/bit-team/backintime/blob/dev/FAQ.md) | GUI, hard-link backups, repeated schedule based on last success | Arrange a suitable source mount and ZFS snapshot selection |
| [restic](https://restic.readthedocs.io/en/stable/) | Versioned encrypted repositories, content deduplication | Arrange access to the remote source and workstation-driven execution |
| [systemd timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) | Persistent calendar timers and catch-up after suspend | Application must track successful completion; timer execution alone is not success |

Napback deliberately uses [rsync](https://rsync.samba.org/ftp/rsync/rsync.1)
for transfer and hard-link reuse. It does not invent a transfer protocol or a
storage format requiring a proprietary reader. The small orchestration layer
selects sources, checks whether work is due, creates a fresh staging directory,
verifies transfers and publishes the directory atomically.

[OpenZFS snapshots](https://openzfs.github.io/openzfs-docs/Basic%20Concepts/Datasets/Snapshots%20and%20Clones.html)
are exposed through `.zfs/snapshot`. The client triggers automount on the host
before an optional Docker sender binds the exact snapshot directory. This avoids
relying on automatic propagation of newly mounted snapshots into a long-lived
container. [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)
support read-only source exposure.

The NAS does not need a new service, privileged container or scheduled script.
A container is an optional packaging choice for rsync. The existing TrueNAS
snapshot scheduler remains responsible for creating and retaining NAS snapshots.
