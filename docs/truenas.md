# TrueNAS setup

Run `napback setup` on the PC for guidance at every input (German), or
`napback setup --language en` for English. Type `?` for detailed help. The default view uses short action hints.
The SSH host is a **TrueNAS user plus NAS address**, e.g. `backup@192.168.1.10`;
it is not the full web-interface URL. The separate SSH key field takes the
private-key path on the PC, not its contents and not the `.pub` file.

Enable **System > Services > SSH**. In **Credentials > Users** (or **Local Users**
depending on the version), check the account's SSH/shell access and **Public SSH
Key**. Only the public key is installed on TrueNAS. For unattended use, both
SSH and any configured `sudo -n` commands must work without password prompts.
The corresponding Sudo Commands settings are also on the user form. Verify the
NAS server fingerprint independently before accepting a first connection.
See the official [SSH service guide](https://www.truenas.com/docs/scale/25.10/scaletutorials/systemsettings/services/sshservicescale/)
and [user settings](https://www.truenas.com/docs/scale/25.10/scaleuireference/credentials/usersscreen/).

Snapshot tasks are under **Data Protection > Periodic Snapshot Tasks**. A naming
schema of `auto-%Y-%m-%d_%H-%M` matches the wizard's default `auto-` prefix.
For existing names starting with `autosnap_`, enter that prefix instead; `*`
allows all names. A matching snapshot generation must already exist, cover all
selected children recursively, and be at most 48 hours old by default. Creating
a periodic task alone does not ensure its first scheduled snapshot has run.

A parent selection includes every child dataset. Napback currently cannot
automatically inherit exclusions from a TrueNAS snapshot task. A child excluded
from snapshots can therefore prevent a backup of the parent; do not remove
system-dataset exclusions merely to bypass this check. Select the intended data
datasets explicitly instead.

New setup defaults to `zfs_raw`: all selected datasets must already have native
ZFS encryption. This mode reads even locked datasets through `zfs send -w -p`;
it needs NAS ZFS send permissions and `timeout`, but no Docker or rsync sender.
Restore needs a compatible ZFS pool and the original keys. See
[encrypted storage and recovery](encryption.md).

1. Create or reuse a TrueNAS periodic snapshot task for each selected dataset.
   Enable recursive snapshots when child datasets are included. A daily task
   with a retention of several days is a reasonable starting point; large first
   backups may require longer retention.
2. Configure SSH key access through TrueNAS. Verify the server host key on the
   PC. The account must be able to list ZFS datasets/snapshots and read all files
   in the selected snapshots. If using an administrator account with passwordless
   sudo, set `sudo: true`. An unprivileged read-only account is preferable where
   the source permissions allow it.
3. For the alternative `files` mode, optionally build the Docker sender on the NAS. Keep the build recipe in a
   persistent dataset, outside an application's generated Compose directory:

   ```sh
   sudo docker compose -f /mnt/tank/tools/napback/docker/compose.yaml build
   ```

4. On the PC run `napback setup`, choose sources and an empty local target, then
   set the Docker image if used. Run `napback plan` before the first transfer.

Napback issues read-only `zfs list/get` commands and `zfs send -w -p` for raw
storage. Files mode accesses `.zfs/snapshot/NAME` through rsync.
Backup never creates, destroys or holds production snapshots,
changes snapshot schedules, restarts applications or modifies source files.
Snapshot consistency is provided by TrueNAS/ZFS, not by Docker.

The container has no ports, no host networking, no persistent writable layer,
no host Docker socket, and only a read-only bind of the current source snapshot.
It needs DAC_READ_SEARCH to read source files regardless of their owner. The
Docker host must support this capability. The PC starts it over SSH, using
`docker run --rm -i`. Both rsync ends have an inactivity timeout, and a separate
container-side timeout limits total sender lifetime after a disconnected client.

The source image is built locally. If Docker removes unused images, rebuild it
from the persistent recipe. An app update cannot overwrite the PC's config or
service units. There is no NAS Apply-script or cron job because there is no
running app to patch. Rebuild periodically to obtain base-image and rsync fixes.

Do not copy an administrator's private key to the NAS or the container. The PC
uses its normal SSH key. Docker access is root-equivalent; the container's
read-only mount does not restrict the SSH account outside that container.

## Dataset boundaries

A parent filesystem snapshot does not contain child filesystem contents.
Napback enumerates children and backs up each from the same snapshot name. It
fails if any child lacks a common snapshot, rather than publishing an incomplete
parent tree. Selecting `recursive: false` explicitly excludes children.

In files mode, children with custom mountpoints cannot be reliably reconstructed by their
dataset names. Configure each custom-mounted filesystem as a separate named
source with `recursive: false`, or select a tree with conventional mountpoints.
Zvols are unsupported. Encrypted-but-locked datasets work in raw mode, but cannot
be backed up as ordinary files.

For files mode, TrueNAS NFSv4 ACLs require separate consideration. POSIX ACL support in rsync
does not provide a full NFSv4 ACL round trip. Keep native ZFS replication or an
appropriate ACL/configuration export when exact SMB permissions are required.
Raw ZFS streams preserve native filesystem metadata; TrueNAS share/user settings
and pool configuration still need separate backups.
