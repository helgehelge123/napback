# Validation record

Validation performed on 2026-09-20 for version 0.1.0. Tests use synthetic data;
production applications were not stopped and production datasets were not copied
or modified during validation.

## Automated suite

- **47 tests passed** on CachyOS, Python 3.14.7, rsync 3.5.0.
- **47 tests passed** in an Ubuntu 24.04 container, using the distribution's
  Python 3.12 and rsync 3.2.7 packages, running as an unprivileged user.
- Local statement coverage: **90% overall**, 90% core, 93% CLI, 97% inventory.
  The separate local transport subprocess is exercised but is not included in
  that process's coverage measurements.
- Ruff checks and formatting pass. Generated systemd units pass
  `systemd-analyze --user verify`.

The suite covers version history, checksum detection of same-size/same-mtime
changes, hard-link reuse, symlinks, extended attributes, metadata-only changes,
retention, source overlap, missing mountpoints, wrong repository identity,
concurrency, low disk space, unknown repository contents, invalid configuration,
source verification failures, offline corruption detection, restore destination
protection, interruption and retry, publication recovery, interrupted retention,
clock corrections, recursive snapshot selection, missing child snapshots,
source freshness, setup and installer behavior, and source-process timeouts.

Failure injection includes command failures representing a full disk, network
loss and process interruption. A physically full filesystem and actual power
loss were not induced. Those are simulation tests, not hardware tests.

## Real TrueNAS / Docker / SSH checks

A dedicated synthetic ZFS parent dataset and child dataset were created on a
TrueNAS SCALE host. A fixture included regular files, a root-owned 0600 file,
a user xattr, a symlink, a FIFO, Unicode/newline filenames, and a 32 MiB random
payload. Three recursive source snapshots were created while the live files
changed between generations.

Verified end to end:

1. Docker source image built successfully; rsync 3.5.0 ran without a network,
   using only a read-only bind of the selected snapshot.
2. The client selected a common parent/child snapshot and restored the original
   snapshot contents even after the corresponding live files changed.
3. Normal file restore reproduced contents, symlinks and user xattrs and passed
   a checksum comparison.
4. A real SSH process was killed during an active throttled transfer. No new
   completed backup appeared; existing inventories still verified.
5. The interrupted Docker sender terminated with the independent timeouts. An
   earlier test exposed an orphaned sender; the timeout fix was retested.
6. The subsequent unrestricted retry completed, cleaned owned incomplete work,
   preserved the old file version and reused hard links for unchanged files.
7. Direct SSH transfer with the host's rsync 3.2.7 also completed and verified.
8. A privileged restore with container rsync 3.5 restored uid 0, mode 0600,
   xattrs, the symlink and the FIFO.

The compatibility test initially found incorrect local `-M` handling in older
rsync builds. Napback now uses a local process transport for separate sender and
receiver settings. The full suite passed on both rsync generations afterward.
The manual privileged restore procedure documents its rsync 3.5 requirement or
the tested Docker alternative.

## Real systemd checks

A separate test user service and timer ran against synthetic local data:

- Starting the timer triggered and completed a backup.
- Only the test manifest's success timestamp was changed to 25 hours in the
  past; the actual system clock was untouched.
- The next scheduled minute automatically created the overdue backup.
- A subsequent run within the interval returned `not_due` and made no version.
- Test timer stopped after validation; no production schedule was activated.

Actual suspend/resume and a physical PC reboot were **not** performed. Catch-up
behavior is based on systemd's documented calendar timer semantics and verified
unit configuration. The 24-hour comparison is tested at its exact boundary.

## Reproduce

```sh
python -m pip install -e . pytest
python -m pytest -q
```

For older-rsync compatibility, mount this repository read-only into the test
container. Run from the repository root:

```sh
docker build -f tests/Dockerfile.compat -t napback-tests:ubuntu2404 .
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  --mount type=bind,src="$PWD",dst=/source,readonly \
  napback-tests:ubuntu2404
```

For NAS tests, use a dedicated synthetic dataset and a separate destination.
Never point interruption or mutation tests at production data. Retain the test
logs and verify both the restored contents and the old versions after failure.
