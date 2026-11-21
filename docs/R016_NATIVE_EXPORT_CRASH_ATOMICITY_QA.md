# R0.16 native PRIVATE JSONL publisher: real abrupt process termination

This is an independent **Draft research QA child** of PR #142, pinned
to its exact source SHA 12a2d75bbdf4605f18cdc87111c9b71b62272dd9.
No production source database, parent branch, main, Android device, or
physical actuator is touched. The tests only create disposable synthetic
one-agent segmented v4 SQLite and unencrypted PRIVATE local JSONL.

## Threat and acceptance

The application exports by replaying a read-only SQLite Backup API
snapshot under a pinned original journal head, generating a verified
bounded JSONL in memory, writing it into a local mode-0600 temporary
file, flushing and calling fsync, then using a same-directory atomic
hard link with no-clobber semantics to publish a new destination.

An unexpected **process termination** can happen before fsync, before
the hard link, or immediately after the hard link but before cleanup.
We test all three using a genuine separate Python process and real
`os._exit`, not only mocks that allow normal `finally` cleanup.
We deliberately intercept exactly the fsync/link syscall boundaries
in the child process while running the real exporter logic:

- Before fsync (exit 70): final destination MUST be absent; original
  source database must not be changed.
- Before hard-link publication (exit 71): final destination MUST be
  absent; original source unchanged.
- Immediately after successful hard-link publication (exit 72):
  destination MUST be a complete newline-terminated, 5-frame JSONL
  whose exact frame-byte SHA-256 matches its metadata, whose
  checkpoint/2 policy/head/count match the real original source,
  and whose file mode is 0600; original SQLite unchanged.
- An intentional **orphan PRIVATE stage file** may survive process
  death, and MUST be treated as secret data, not an automatically
  cleaned harmless log. Tests inspect 0600 permissions and delete
  only their own temporary files. Operators must quarantine/reconcile
  older orphan stages, never assume crash cleanup ran.

## Concurrent publication and stale head

Two actual independently launched exporter processes contend for the
**same absent final destination** using the same externally pinned
head and original SQLite. Exactly one must succeed and one must fail,
and the final file must be complete; the losing process may see
either the preflight already-existing destination or the atomic hard
link EEXIST race. Nothing may truncate or replace the winning file.

A separate real source writer commits one new frame **after** the
original head was externally recorded. An attempted export using the
stale head must fail with NO published JSONL; the legitimate newer
source must remain intact and independently replayable under its new
head. This is rejection of an outdated source anchor, not a real
rollback attack defense.

All subprocess tests are Linux/POSIX-only for exact hard-link/file-mode
and process exit semantics. The workflow executes this suite under
Python 3.11, 3.12 and 3.13, with no internet access during the test.

## What is NOT proven

- An actual kernel panic, power failure, hardware write-cache loss or
  Android flash filesystem durability. `fsync` on the file does not
  independently guarantee persistence of the parent directory entry;
  a future explicit directory-fsync protocol and real power-cut test
  would be required for stronger on-disk durability claims.
- Cryptographic identity: the JSONL's in-file SHA-256 can be forged
  together with frame bytes; a separately trusted original head or
  signed/anchored manifest is required.
- Fully safe eviction of stale staging files, physical segmentation,
  two-node replication, unlimited v4 motor ticks or genuine brain
  functions. The #143 sealed-archive project is separately testing
  physical immutable segment files; this PR does not duplicate it.

No merge/production permission is conferred by CI, and all existing
parents remain Draft for independent account-distinct review.
