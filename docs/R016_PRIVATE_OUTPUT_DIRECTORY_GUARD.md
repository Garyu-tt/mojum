# R0.16 PRIVATE trace exporter: owner-only output directory

**Draft / research-only**, stacked on [PR #152](https://github.com/Garyu-tt/mojum/pull/152).
This changes only PRIVATE trace publication preflight and does not alter
SQLite journals, existing cached memories, learned-model state, v4 clocks
or versioned payload schema.

## The problem

The native exporter stages JSONL in an output directory chosen by the operator.
It enforces POSIX `0600` on the published **file**, and the downstream
[PR #155](https://github.com/Garyu-tt/mojum/pull/155) read-only
orphan-stage inspector already requires an explicitly owner-only **directory**.

But before this change the exporter accepted a 0755/0777 directory or
one owned by another account. File mode prevents casual reads of payload
bytes, but the shared directory may reveal filenames, allow other
participants to alter namespace entries, or make private-stage incident
handling unsafe. A direct symlink of the parent was already refused;
an ancestor symlink could still be used to hide a redirect.

## Policy

On Linux/POSIX, explicitly exporting PRIVATE JSONL now demands the
output's **actual supplied parent directory**:

- Is an existing directory, not a symlink itself, with **no symlink /
  alias ancestors** under the supplied path.
- Is owned by `os.geteuid()`.
- Grants all owner `rwx` directory permissions, and **no** group/other
  permissions. Canonical mode `0700`, other owner-only special-mode
  bits may be present.
- Contains a previously **absent** output filename.

If any check fails, the exporter raises before reading source event
contents and **before allocating a PRIVATE JSONL temporary file**.
It never changes permissions of an operator-controlled directory or
deletes unexpected files.

On non-POSIX platforms, this patch does not invent a cross-platform
permission model. Current source/JSONL checks are otherwise unchanged.
This intentionally rejects exporting directly to typical public
`/tmp`, shared workspaces and group-readable folders; create an
explicit 0700 private export folder instead.

## Real test coverage

Six tests run on CPython 3.11/3.12/3.13 and real disposable segmented-v4
SQLite. They exercise a valid native 0700→0600 export; refusal of
0755, 0750, 0705, 0770, 0777, 0600 and 0300 directories; simulated
wrong owner without chown; a symlink in an ancestor with a genuine
non-symlink final parent; a directly symlinked parent; and no-clobber.
Each negative test asserts zero destination file, zero native staging
files, unchanged source SQLite bytes and no auto-chmod of the directory.

All tests use only synthetic PRIVATE fixtures, no real user databases,
no network, no production release, no uncontrolled filesystem changes.

## Boundary / nonclaims

This check is a fail-closed **path preflight**, not an OS-level lock,
`openat2` sandbox or a complete defense against a hostile actor who can
replace parent directories after the check. Strong path anchoring would
require maintaining a directory file descriptor and using dirfd-based
temporary publication throughout the operation; currently the
directory must be trusted and under owner control.

Inherited Draft #152 adds POSIX inode-mode→file-fsync→hardlink→unlink→
directory-fsync ordering but cannot by itself prove electrical power-cut
durability on physical hardware. No cryptographic source authentication,
two-node redundancy, sensitive-data encryption or actual Android
filesystem test is claimed. Keep #116 writable v2A/v2B ABI conflict
open. Requires independent review, exact-head green CI and owner approval;
do not merge/main from green unit tests alone.
