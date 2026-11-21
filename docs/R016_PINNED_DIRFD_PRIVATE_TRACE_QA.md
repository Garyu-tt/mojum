# R0.16 — anchored POSIX private JSONL publication

**Status:** isolated Draft security research PR, based on #159.
Only synthetic disposable PRIVATE SQLite fixtures were used. No existing
owner data, live Noosphere, Android device, service, or `main` is modified.

## Verified risk

Earlier exporter preflight checked that a PRIVATE output directory was
owner-owned 0700 and that no ancestor was a symlink. But publication
later used pathname-based `tempfile.mkstemp(dir=parent)`, `os.link`,
`os.unlink` and a fresh pathname-based directory `fsync`. If that
directory's pathname changed between validation and publication, those
calls could operate on different actual directory inodes. A test of
permissions *before* export is not sufficient for race resistance.

## New POSIX protocol

After full SQLite Backup API snapshot, deterministic cold replay,
head/epoch checks and JSONL checksum verification:

1. Walk the directory **one component at a time** with `O_DIRECTORY`,
   `O_NOFOLLOW` and `dir_fd`, rejecting any `..` component or
   symlink at any depth. Check final `fstat` owner against effective
   UID and required owner-only `0700` permission model. Keep the
   resulting file descriptor open until the entire publication finishes.
2. Re-check absence of the target with `os.stat(...,
   dir_fd=directory_fd, follow_symlinks=False)`. A pre-existing file,
   including a symlink, is never overwritten.
3. Create the random PRIVATE stage name using `secrets.token_urlsafe`
   and `os.open(O_CREAT|O_EXCL|O_NOFOLLOW, 0600, dir_fd=directory_fd)`.
   Retry at most eight true collisions; never clobber a collided name.
4. `fchmod` the open stage descriptor to 0600; write and flush the
   verified bytes; `fsync` the same file descriptor.
5. Re-open/verify that the output pathname still resolves to the
   **same validated directory inode**; if it was renamed/replaced,
   fail closed before publishing. This is in addition to the pinned
   original directory fd.
6. Atomically `os.link` the new stage basename to the final basename,
   with **both `src_dir_fd` and `dst_dir_fd` referencing the exact
   same original private directory inode**, no symlink following and
   no-clobber. A competing file appearing after the earlier check
   causes `EEXIST`.
7. Remove the stage name via `os.unlink(..., dir_fd=directory_fd)`.
   After success, `fsync` the **same already-open directory fd** to
   request persistence of both the final link and stage unlink.
8. Re-verify the currently visible pathname's directory inode before
   acknowledging success. If the parent was moved, error and require
   **manual reconciliation** of the complete output in the original
   pinned directory. Close every parent descriptor, including errors.

On non-POSIX, retain existing basename/pathname fallback and explicitly
do **not** promise anchored-dirfd or directory-fsync semantics.

## Fault injection and actual test evidence

The new `tests/test_r016_private_dirfd_publish.py` creates a genuine
disposable segmented-v4 SQLite journal and actively injects:

- Rename owner-only output directory and replace its original path
  with a symlink to a different owner-only directory **right before**
  the atomic link. The PRIVATE JSONL must NOT appear in the substitute
  directory; an already created file may be in the original moved
  directory, but the operation must refuse success.
- Insert that same substitution immediately **between initial
  preflight and the actual anchored open**: `O_NOFOLLOW` rejects it
  without publishing anywhere.
- Create a competing target file immediately before the atomic link:
  no overwrite, private stage cleaned, source unchanged.
- Repeatedly collide the proposed secret stage basename: fail after
  bounded retries, preserve the victim bytes and do not publish.
- Retain parent directory descriptors during real success and error
  checks: all must be closed, with `fstat` raising `EBADF` afterward.

The inherited #146 process-crash test fixtures were minimally adjusted
to preserve the intended exit-before-link / exit-after-link fault
points when `os.link` receives `src_dir_fd` and `dst_dir_fd`
keyword arguments. Nothing was skipped.

On `mirbi-linux`, a fresh git clone of exact commit
`a4388b7e3fd25aedfe223cf262cd6b2f703ae0d8` was tested as
an unprivileged uid 65534, Python 3.13.5, sanitized environment,
disposable temporary local directory and bounded runtime:
**30/30 `test_r016_*.py` methods PASS**, including both the original
crash tests and new adversarial checks. This is *not* GitHub Actions
evidence; new exact-head CI must separately run Python 3.11–3.13.

## Python 3.13 whole-suite SQLite lifetime audit

On the original PR #164 combined code before the LocalNoosphere fix,
the *whole* Python 3.13 `unittest discover` run passed 380 tests but
reported `ResourceWarning: unclosed database`. An independent
`sqlite3.connect/handle` auditing hook with per-test forced GC
pinpointed three real constructor failures:

- `test_noosphere.NoosphereTests.test_different_agent_rejected`
- `test_segmented_aquarium.ExplicitSegmentedAquariumQA.test_old_journal_schema_and_wrong_size_refuse_without_rewrite`
- `test_segmented_noosphere.AtomicSegmentQA.test_old_local_noosphere_and_segmented_db_refuse_migration`

They all exercise `LocalNoosphere.__init__` rejecting wrong persisted
owner/schema while holding the newly constructed SQLite connection.
The upstream independent [Draft #124](https://github.com/Garyu-tt/mojum/pull/124)
already contains the correct fix and strong-reference regression tests.
Its precise exception-safe constructor close block and
`tests/test_noosphere_init_close.py` were **reused**, not re-invented,
on this downstream experimental integration branch. In addition,
the two disposable SQLite corruption-fixture contexts in
`tests/test_segmented_noosphere.py` now combine `contextlib.closing`
with the original transaction context manager, preserving COMMIT and
closing each handle before leaving the fixture.

After both fixes, an exact-head disposable checkout was run as uid
65534 (`nobody`), CPython 3.13.5, from `mirbi-linux`:

- **366/366 fast tests**, one process, forced `gc.collect()` after
  each test and `ResourceWarning` interception: zero errors, zero
  unclosed SQLite warning events (three test skips within this count).
- **384/384 whole-suite tests** including the real 100,001-event
  segmented SQLite stress, v4 10,050-tick epoch replay, >4,000
  observed contacts and prior v4 PRIVATE exporter process/dirfd races:
  **zero failures, zero errors, zero `ResourceWarning`** with
  `PYTHONWARNINGS=always::ResourceWarning`; three tests skipped.
  One-run wall time was 176.429 seconds on the CPU-first owner node.

`tests/audit_sqlite_resource_warnings.py` is the source-identical
upstream #124 whole-suite regression harness. The linked CI workflow
runs this on Python 3.13, in addition to the focused Python
3.11–3.13 matrix, and fails if any unclosed database is detected
after final GC. GitHub Actions **must independently confirm** this
on the final exact commit; local success alone is not release approval.

## Remaining limits (do not claim full security)

- We do not control a malicious actor who can rename the parent
  again **after** final re-validation and successful return. However,
  all actual writes remain bound to one previously authorized
  owner-only directory inode, and false success on a detected
  mid-publication swap is prevented.
- O_NOFOLLOW/dirfd semantics are Linux/POSIX-specific. Symlink tricks
  by privileged actors, mount point replacement, hardlink privileges,
  a compromised same-UID process or exotic filesystems require
  separate threat modeling; this is not a replacement for sandboxing
  or an OS-provided authorization policy.
- `fsync` requests persistence; it does not **prove** survival of real
  AC loss, hardware write-cache failure, or remote filesystems.
- The JSONL frame SHA is stored in the same file and is not an
  independent signature. Source heads must be independently anchored.
- No auto-cleanup of PRIVATE orphans, real hardware QA, encryption,
  version-ABI migration, autonomous life or immortal motor ticks are
  asserted. All parent PRs remain Draft; owner review/CI are required.
