# TrueNAS setup

1. Create or reuse a TrueNAS periodic snapshot task for each selected dataset.
   Enable recursive snapshots when child datasets are included. A daily task
   with a retention of several days is a reasonable starting point; large first
   backups may require longer retention.
2. Configure SSH key access through TrueNAS. Verify the server host key on the
   PC. The account must be able to list ZFS datasets/snapshots and read all files
   in the selected snapshots. If using an administrator account with passwordless
   sudo, set `sudo: true`. An unprivileged read-only account is preferable where
   the source permissions allow it.
3. Optionally build the Docker sender on the NAS. Keep the build recipe in a
   persistent dataset, outside an application's generated Compose directory:

   ```sh
   sudo docker compose -f /mnt/tank/tools/napback/docker/compose.yaml build
   ```

4. On the PC run `napback setup`, choose sources and an empty local target, then
   set the Docker image if used. Run `napback plan` before the first transfer.

Napback issues read-only `zfs list` commands, accesses `.zfs/snapshot/NAME`, and
reads through rsync. It never creates, destroys or holds production snapshots,
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

Children with custom mountpoints cannot be reliably reconstructed by their
dataset names. Configure each custom-mounted filesystem as a separate named
source with `recursive: false`, or select a tree with conventional mountpoints.
Zvols and encrypted-but-locked datasets cannot be backed up as ordinary files.

TrueNAS NFSv4 ACLs require separate consideration. POSIX ACL support in rsync
does not provide a full NFSv4 ACL round trip. Keep native ZFS replication or an
appropriate ACL/configuration export when exact SMB permissions are required.
