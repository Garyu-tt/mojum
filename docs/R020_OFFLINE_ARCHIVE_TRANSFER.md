# R0.20 — explicit owner-verified cold archive transfer to another filesystem

Research-only **Draft** built on [PR #153](https://github.com/Garyu-tt/mojum/pull/153).
No original PRIVATE Noosphere, production host, main branch, connected
cloud, Telegram, user backups or device mounts are modified.

## Purpose

R0.19 can verify two archive directories against an independently
pinned owner-HMAC receipt, but it does not COPY the files. The new
`copy_pinned_archive_to_device(...)` performs an **explicit**
offline transfer of a selected, entirely verified checkpoint.

A copied checkpoint consists of **only** the signed exact sealed
segments and the single selected incomplete tail; it does **not**
include signing key, receipt, other historic snapshots or arbitrary
unrecognized files. The operator chooses destination storage.
The original source is never deleted, compacted, overwritten or
reclassified as a replica.

## Safety gate

- The destination **already exists**, is owner-only 0700, cannot be
  a symlink and is not the source directory.
- **Same filesystem `st_dev` is rejected by default.**
  The two VFS `st_dev` values must differ before copying any PRIVATE
  event bytes. This can reject bind mounts sharing one filesystem.
  A different ID proves **only a different mounted filesystem as
  seen by the OS**, NOT two independent physical storage devices.
  A tmpfs, loop device, partition or remote mount may still be
  in the same host/power failure domain.
- PRIVATE copy must be explicitly authorized with
  `include_nonpublic=True` / `--include-private`.
- A 32-byte owner-only HMAC key and its immutable receipt must
  remain **outside both archive directories**. Caller must present
  the correct **independently pinned receipt SHA-256 digest**.
  No authority is inferred from the archive's own manifest.
- The entire source checkpoint is authenticated and replay-verified
  before a single destination byte is written. The receiver verifies
  both source and target against the same signed statement afterward.
- Each selected file is read through no-symlink, regular-file,
  owner-only checks in bounded 128 KiB chunks and a 64 MiB per-data
  file limit; transferred files use no-overwrite atomic publication
  and `fsync`. No partial member is silently reported complete.
- A pre-existing complete destination is **verified, not rewritten**
  (idempotent). Unexpected files or an incomplete copy are rejected
  for manual operator quarantine. Failed transfers never automatically
  delete or repair possibly valuable evidence.

## Explicit CLI

On a Linux host with an authorized owner-only source archive, a
pre-existing owner-only archive directory located on a *different
mounted filesystem*, and an independently protected 32-byte signing
key plus pinned receipt SHA-256:

```bash
PYTHONPATH=src python -m mojum.segment_archive_cli copy-device \
  --source-archive /private/archive-A \
  --destination-archive /mounted-other-filesystem/private-archive-B \
  --receipt-file /private/receipts/checkpoint.json \
  --key-file /private/owner-32bytes.key \
  --expected-receipt-sha OUT_OF_BAND_PINNED_RECEIPT_SHA256 \
  --include-private
```

Only minimal summary metadata is printed: source event count,
source head, selected file count, successfully copied bytes and
the limited distinct-VFS-ID check. No PRIVATE events or file paths
are printed. Never publish PRIVATE plaintext archive to a public
volume or GitHub artifact.

## Real and simulated tests

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_archive_device_transfer.py -v
```

Tests use only synthetic PRIVATE 130-event SQLite fixtures, including
128 sealed + 2 tail and owner-HMAC receipt. They check:

- Actual same-filesystem source/target **rejected with zero copied
  PRIVATE files**.
- Actual file copying and idempotent independent content verification
  with **simulated** distinct `st_dev` IDs (unit mock). This
  simulates the *branch*, not hardware independence.
- On Linux CI, if `/dev/shm` is a separate mounted VFS, an **actual**
  CPU subprocess executes the `copy-device` CLI from a temporary
  owner-only directory to an owner-only `/dev/shm` directory.
  The transfer must fail without `--include-private` and succeed
  with it. If that mount is absent, the test explicitly skips.
  This proves a cross-filesystem copy, not two physical machines.
- Wrong HMAC key/digest, changed source, changed target, missing
  member, unexpected target file, key embedded within plaintext
  archive, and deliberate exception after the first committed
  copied member. Interrupted copies fail closed and never rewrite
  old PRIVATE source/journal.

## Open architecture gates

The feature is **synchronous**, opt-in and one-way; it neither starts
a scheduler nor replicates new events after the selected checkpoint.
There is no encrypted transfer/at-rest encryption, owner key
rotation, remote identity challenge, authenticated independent
receipt custody, quorum, writer leasing, partition handling, or
real-world device power-cut/failover proof.

One host's separate `st_dev` values do NOT establish independent
power, media, hardware, or administrative failure domains.
A genuine two-device acceptance still requires actual distinct
authorized devices/volumes, independent custody of root hashes and
read-back after source device is unavailable. Tracked in
[Issue #141](https://github.com/Garyu-tt/mojum/issues/141).

All work remains Draft and must pass exact-final-SHA CI and
independent security/data recovery review before any merge.
