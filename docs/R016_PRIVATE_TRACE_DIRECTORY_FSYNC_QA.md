# R0.16 native PRIVATE JSONL: POSIX directory-fsync publication

Status: **Draft**, stacked research-only on crash/competition QA #146.
No main branch merge, live source migration, file deletion outside temporary
tests, user PRIVATE source disclosure, physical device, or owner-approved release.

## The exact filesystem contract

The native `mojum.aquarium_trace_export` already creates a fresh
mode-0600 local temporary file, writes and `fsync`s the complete
replay-verified JSONL bytes, then atomically hard-links that complete
file to a **new** absent destination, refusing preexisting outputs.

There is a less obvious durability gap: `fsync` of **the file** alone
does not request persistence of the final pathname in **its parent
directory**. This change calls `_fsync_published_directory(dest.parent)`
only after successful hard-link publication **and** unlinking the
temporary PRIVATE staging name. On POSIX it opens the actual trusted
parent directory (using O_DIRECTORY/O_NOFOLLOW where supported),
verifies `fstat` identifies a directory, `fsync`s its descriptor,
and closes it explicitly.

**Successful POSIX return therefore follows this ordering:**

1. Complete JSONL bytes are already verified against a fully cold
   replayed original SQLite, with an independent source-head check for v4.
2. The stage inode is already 0600 from `mkstemp`; reinforce its owner-only mode via **`fchmod` on the actual open file descriptor** (POSIX), **before** any file sync. Non-POSIX retains `chmod` by pathname before file fsync.
3. Write, flush and **file-fsync bytes plus inode mode metadata**.
4. Atomically no-clobber hard-link output.
5. Unlink PRIVATE staging pathname.
6. Open/verify/fsync/close the output **directory**.
7. Only then return success metadata.

This does not add an auto-overwrite, auto-restore or auto-cleanup
process, and it does not change old journal formats, v4 learning,
motor policy, epoch markers, or output contents.

## Failures: never confuse visible with durable

- An `os.link` EEXIST race means the existing final output is never
  replaced; temporary output is removed in normal cleanup.
- A process killed before publication may leave PRIVATE orphan
  `.mojum-private-trace-*.jsonl` staging files. They remain secret.
  Explicitly quarantine and reconcile them; never blindly delete
  files matched by a glob on a live user filesystem.
- A process killed after the link but before directory sync may leave
  a completely valid *visible* output. That does NOT imply the name
  survived an actual loss of power.
- If parent-directory `fsync` fails, the exporter **raises** and never
  reports success. The complete final output may already exist; it is
  deliberately left intact for operator reconciliation, not
  overwritten by a retry.
- On non-POSIX the method reports no directory-sync guarantee and
  retains prior behavior; the proposed QA is Linux-only.

## Executed acceptance definition

- CPython 3.11/3.12/3.13, actual synthetic segmented-v4 SQLite source,
  real native exporter, and no network or service.
- Instrumented actual OS-level `fchmod` and `fsync` calls verify
  **private inode mode first, file fsync second, directory fsync last**;
  stage pathname has already been unlinked by the time of final directory sync.
- Injected `EPERM` on inode-mode hardening or `EIO` on file fsync
  must reject publication altogether, with original SQLite unchanged.
- Injected `EIO` on directory `fsync` must raise with a complete,
  mode-0600 and no-clobber final output still present. It must not
  publish a false success or change original SQLite.
- Existing output refuses before any fsync.
- Separate real Python process forced via `os._exit(73)` immediately
  before the parent directory sync; its visible final file remains
  valid but no durability acknowledgment is returned.

Inherited crash tests #146 cover before-file-fsync, before-hard-link,
after-hard-link, two competing exporters and corrupted segmented
SQLite backup. Parent native exporter #142 has independently passed
11/11 exact-head workflow groups, but a new QA child must pass its
own exact-head tests before claiming acceptance.

## Important nonclaims

Directory fsync **requests** Linux/POSIX filesystem namespace
persistence. It does not prove resilience against actual AC loss,
faulty drive/flash firmware, a lying disk write cache, unsupported
network filesystems or remote Android APIs. Hard-link support
depends on same-filesystem POSIX semantics. A SQLite-backed source
may change its raw file header on unrelated normal writable open;
logical event-chain equality is the correct post-reopen guarantee.

An in-file SHA-256 is not a cryptographic signature. The owner needs
independently anchored journal heads and eventually two physically
independent encrypted copies for real continuity. No data is uploaded
or transmitted by these tests. Main and parent PRs remain untouched,
and release depends on independent human review and explicit approval.
