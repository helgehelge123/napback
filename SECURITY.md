# Security model

The local user and configuration are trusted. Do not load a configuration from
an untrusted source: SSH options can intentionally select a ProxyCommand, and
NAS configuration can authorize Docker/sudo access. Napback never executes a
local shell for transfer commands. Remote arguments are shell-quoted.

SSH uses verified host keys and batch mode. Private keys stay in SSH's normal
storage. Do not put passwords or tokens in configuration files. The repository
is private to the user (0700); extended attributes can contain source ownership
and access-control metadata.

The default setup mode, `zfs_raw`, preserves native ZFS encryption through
`zfs send -w -p`. It rejects unencrypted source datasets and never falls back to
plaintext. Only encrypted streams are written to the local archive; no local
decryption key or plaintext staging directory is needed. Keep original ZFS keys
separately for recovery. Dataset/snapshot names, GUIDs, timestamps, sizes and
some properties remain visible. See [encryption](docs/encryption.md).

Existing jobs and explicitly selected `files` storage use rsync and contain
readable file contents. This mode has no encryption at rest; only transport is
encrypted by SSH. Repository markers prevent mixing the two storage modes.
Switching modes requires a new repository and does not remove old plaintext.

In files mode, Docker mounts the selected source read-only, disables networking, drops all
capabilities except DAC_READ_SEARCH (needed to read root-owned source files),
and enables no-new-privileges. No host Docker socket or device is mounted inside
the container. The SSH account still has whatever privileges its NAS account
has; Docker access itself is root-equivalent. For least privilege, use a dedicated
SSH account that can only read the selected files/snapshots, with direct rsync and
`sudo: false`. An account confined to a forced rsync command alone cannot run the
ZFS discovery commands; a tailored allowlisted gateway would also be required.

OpenZFS, SSH, host and container rsync must be kept updated. Paths and rsync output are handled
as untrusted filenames, but this project does not claim to safely consume a
malicious or compromised rsync server. Source symlinks are not followed.

Repository markers and expected mountpoints prevent ordinary wrong-disk and
unmounted-disk mistakes. They are not cryptographic device authentication. A
malicious local user with write access can alter the inventory and its manifest
or every hard-linked copy. SHA-256 inventories detect accidental corruption;
they are not authenticated signatures or ransomware protection.

The program deletes only its own marked incomplete attempts and validated old
snapshots. Retention is disabled with `keep: 0`. Never use the repository as a
working directory, mix unrelated files into its internal directories, or run
other writers against it while Napback is active.

Raw restore only receives into new datasets and never uses force rollback or
automatic dataset deletion. Received filesystems remain unmounted. The destination
ZFS host is trusted to receive the stream; validate its SSH host key. Native ZFS
checksums and AEAD protect the encrypted data, but the local inventory itself is
not signed. A corrupted stream can invalidate later dependent versions. Checks
detect corruption, not repair it; retain another independent copy.

Report suspected vulnerabilities privately through the repository owner's
available contact channel. Do not publish credentials or personal backup paths
in public issues.
