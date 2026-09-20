# Validation record

## Version 0.4: local browser interface

On CachyOS, **121 tests passed**, including all three Chromium browser tests.

The new interface was tested with synthetic NAS metadata and real Chromium:
dataset selection, actual snapshot names, missing-snapshot errors, explicit
exclusions, navigating back, folder selection, review, config save, reload and a
390-pixel mobile viewport. No browser JavaScript errors occurred. API tests cover
token authentication, Origin/Host restrictions, review tokens, changed configs,
repository identity preservation, config backups and separate timers for custom
jobs. Invalid exclusions and unsupported volume selections are rejected.

The installed 0.4 interface was also driven through Chromium against the real
isolated encrypted TrueNAS parent/child fixture. It discovered metadata over SSH,
saved a separate configuration, started an independent background backup and
verified the resulting encrypted archive. No production data was transferred and
no NAS settings or snapshot tasks were modified. Screenshots were inspected for
layout; this does not validate every browser or screen reader.

## Version 0.3.2: shorter setup instructions

Validated on 2026-09-20: **101 tests passed** locally. The wizard displays short
action hints by default; `?` opens the detailed help. Existing interactive tests
exercise both views. An installed setup run against the isolated encrypted
TrueNAS parent/child fixture completed successfully with the detailed snapshot
instructions hidden. No backup timer was activated for the test.

Dataset input now explicitly rejects filesystem paths such as `/mnt/tank/data`
with a correction showing the dataset-name form. Snapshot help includes concrete
UI steps for a first snapshot and a daily task. Snapshot-task exclusions remain
unsupported; selecting a parent whose children lack snapshots still fails safely.

## Version 0.3.1: guided TrueNAS setup

Validated on 2026-09-20: **101 tests passed** on CachyOS/Python 3.14.7.
The additional wizard checks cover help, invalid-input retries, explicit key
paths without displaying key contents, custom snapshot prefixes, English mode,
actionable SSH errors and preserving a nonempty destination.

The installed wizard was also run against the existing isolated encrypted
TrueNAS parent/child fixture using real SSH, an explicit key and passwordless
sudo. It listed datasets, validated common snapshots, initialized a separate
empty local repository and saved a configuration accepted by the installed
`plan` command. No production backup configuration or timer was created.
The installed version reported 0.3.1 and the KDE tray service remained active.

## Previous transfer and recovery validation

Validation performed on 2026-09-20 for version 0.3.0, building on the version 0.1/0.2
transfer and recovery tests documented below. Tests use synthetic data;
production applications were not stopped and production datasets were not copied
or modified during validation.

## Automated suite

- **97 tests passed** on CachyOS, Python 3.14.7, rsync 3.5.0.
- **97 tests passed** in an Ubuntu 24.04 container, using the distribution's
  Python 3.12 and rsync 3.2.7 packages, running as an unprivileged user.
- Version 0.1 statement coverage: **90% overall**, 90% core, 93% CLI, 97% inventory.
  The separate local transport subprocess is exercised but is not included in
  that process's coverage measurements. Coverage was not remeasured for 0.2/0.3.
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

Version 0.2 adds unchanged-snapshot skipping beyond 24 hours, GUID changes without
a name change, immediate new-generation backup, legacy-manifest upgrade, failed
check retry, configurable polling boundaries, manual check bypass, status during
an active backup, offscreen tray states/actions and interval edits with backups.

## Installation and desktop checks

- A clean Ubuntu 24.04 container started without Python, rsync, SSH or Qt.
  `install.sh` installed the missing packages via `apt-get`, created the private
  environment, loaded Qt offscreen and prepared desktop autostart without an
  active user bus. The complete 72-test suite then passed as an unprivileged user.
  The first attempt exposed a missing fontconfig library; the corrected package
  dependency set passed the clean installation test.
- The default installer completed on a real CachyOS/KDE Plasma desktop. The user
  tray service ran and KDE's StatusNotifierWatcher registered the Napback item;
  title, active state and unconfigured tooltip were read through D-Bus.
- Automated package-manager dispatch tests cover pacman, apt-get and dnf using
  fake executables. Fedora installation was **not** tested on a real Fedora system;
  CachyOS already had the required system libraries.
- GUI tests use Qt's offscreen backend. They are not a visual test of every desktop
  shell. GNOME tray extensions and other desktop environments remain untested.

Failure injection includes command failures representing a full disk, network
loss and process interruption. A physically full filesystem and actual power
loss were not induced. Those are simulation tests, not hardware tests.

## Real TrueNAS / Docker / SSH checks

A dedicated synthetic ZFS parent dataset and child dataset were created on a
TrueNAS SCALE host. A fixture included regular files, a root-owned 0600 file,
a user xattr, a symlink, a FIFO, Unicode/newline filenames, and a 32 MiB random
payload. Four recursive source snapshots were created while the live files
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
9. With the installed 0.2 client and an actual systemd test timer, a fourth
   recursive snapshot was created while the latest backup was less than 24 hours
   old. The timer detected the changed GUIDs, copied the new parent/child contents
   and produced a valid SHA-256 inventory. A following check returned
   `no_new_snapshot` without creating another version. The test timer was stopped.

The compatibility test initially found incorrect local `-M` handling in older
rsync builds. Napback now uses a local process transport for separate sender and
receiver settings. The full suite passed on both rsync generations afterward.
The manual privileged restore procedure documents its rsync 3.5 requirement or
the tested Docker alternative.

## Real systemd checks

A separate test user service and timer exercised interval mode against synthetic
local data in version 0.1:

- Starting the timer triggered and completed a backup.
- Only the test manifest's success timestamp was changed to 25 hours in the
  past; the actual system clock was untouched.
- The next scheduled minute automatically created the overdue backup.
- A subsequent run within the interval returned `not_due` and made no version.
- Test timer stopped after validation; no production schedule was activated.

Actual suspend/resume and a physical PC reboot were **not** performed. Catch-up
behavior is based on systemd's documented calendar timer semantics and verified
unit configuration. The 24-hour comparison is tested at its exact boundary.


## Version 0.3 encrypted raw storage

A dedicated AES-256-GCM ZFS dataset and child were created with a private synthetic
test key kept on the NAS, outside the public repository. No production keys were
exported or inspected. With OpenZFS 2.3.9 on TrueNAS, the following passed:

1. Full raw parent/child streams copied to ordinary files on a PC without ZFS.
2. A new recursive generation produced incremental streams. Retention of only one
   local generation removed the older directory while keeping its required base
   streams through hard links; chain validation and SHA-256 verification passed.
3. Unique plaintext content markers were absent from archive files. Native raw
   receive and original-key unlock, rather than marker absence alone, establish
   that the format remains encrypted.
4. Full plus incremental streams were actually received into new NAS datasets.
   Both received filesystems were initially locked. The original test key unlocked
   them. File contents, binary payload hash, uid 0, mode 0600, user xattr and symlink
   matched the selected source generation.
5. The first content check used an incorrect mountpoint: the test prepended `/mnt`
   although TrueNAS's pool altroot already supplied it. Reading the actual ZFS
   mountpoint resolved the test failure; the receive itself was correct. Recovery
   documentation now explains this distinction.
6. A new full generation was backed up after unmounting only the synthetic source
   filesystems and unloading their encryption key. The key stayed unavailable.
7. A wrong test key was rejected by the restored dataset; it remained locked.
8. A real throttled raw transfer was terminated with SIGTERM. No incomplete
   generation was published, the previous archive remained verifiable, and a
   subsequent retry completed successfully with intact dependencies.

The automated raw tests cover full/incremental selection, missing or replaced
bases, chain limits, retention, corruption before reuse, producer errors, empty
streams, timeouts, encryption enforcement, storage-mode separation, restore
collision protection and default encrypted setup. A changed incremental base
during transfer is rejected before publication. A simulated same-size ciphertext
write corruption is rejected by comparing readback with the hash computed during
transfer, before any generation is published.

Native encryption and receive are tested on real ZFS; tests using stand-in stream
bytes exercise bookkeeping only. Key rotation, physical power loss and every
possible ZFS feature/version combination remain untested. The archive does not
repair damaged stream files. Tests retained synthetic datasets for inspection.

## Reproduce

```sh
python -m pip install -e '.[tray]' pytest
python -m pytest -q
```

For the full missing-package installer and Qt tests on Ubuntu, run from the
repository root:

```sh
docker build -f tests/Dockerfile.install -t napback-install-test .
docker run --rm --network none napback-install-test
```

For backup-core compatibility without Qt (tray tests are skipped), mount this
repository read-only into the smaller test container:

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
