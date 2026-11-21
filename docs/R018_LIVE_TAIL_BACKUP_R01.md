# Mojum R0.18 — explicit snapshot of unsealed PRIVATE journal tail

Research Draft stacked on [#143](https://github.com/Garyu-tt/mojum/pull/143).
No production Noosphere source data, real phone, VM, Telegram or
cloud storage is opened, moved, deleted or exported.

## Why

Physical archives in R0.17 contain **only complete sealed segments**.
On a 64-event segment size, a 130-event source can archive 128 events;
the 2 unsealed live events are **not** backed up. Losing the original
SQLite would lose that recent part of experience, even though sealed
history could be recovered.

The new opt-in `mojum.noosphere.live-tail-capsule/1` format captures
the current incomplete physical segment at a **specific independently
trusted canonical source SHA-256 head**, with exactly pinned total
source event count. Both head and count are necessary to select an
immutable point-in-time capsule. The source never resets or rewrites
a journal hash, event identifier, timestamp, epoch or learning state.

## Exact format

When a physical archive of complete segments already exists,
`capture_live_tail(store, archive_dir, include_nonpublic=True,
external_expected_source_head=...)` verifies the source and
sealed physical prefix, then publishes two new **0600 owner-only**
files in a preexisting **0700** directory:

```text
segment-00000000.events.jsonl
segment-00000000.manifest.json
segment-00000001.events.jsonl
segment-00000001.manifest.json
tail-000000000129-000000000130.events.jsonl
tail-000000000129-000000000130.manifest.json
```

The tail is a **snapshot**, not a new canonical live journal segment.
It contains only 1..(segment_size-1) events and never modifies
the original SQLite. Manifest fields pin actor, exact sequence
bounds, segment number, previous sealed head, final source head,
canonical event SHA-chain, physical payload checksum, byte size,
visibility and immutable schema. The original source remains
the canonical writer until explicitly stopped.

Writing data and its manifest are distinct durable publications.
A crash after data creation but before the manifest leaves an
orphan candidate; the next export **fails closed for operator
quarantine**, without silently overwriting, fabricating a
manifest or changing source history.

For a source with **fewer events than one segment size**, a capsule
may describe a tail beginning at sequence 1 without any sealed
prefix. For an exactly sealed source (zero live tail), the old
`export_sealed_segments` is sufficient and this API refuses to
create a zero-length capsule.

## Read-only verification and independent restore

`verify_point_in_time(archive_dir, source_events=N,
external_expected_source_head=HASH)` verifies all sealed physical
segments, the tail bytes, original canonical event envelopes and
hashes and the exact external trusted final head.

`restore_point_in_time(archive_dir, new_path, source_events=N,
external_expected_source_head=HASH)` first verifies all archive
files, reconstructs the **entire pinned prefix** from the archive
alone in an owner-private scratch SQLite, independently checks
the restored global hash chain and count, checkpoints WAL, and
atomically publishes only a **new previously absent** database
path. Old databases and any unselected newer events remain untouched.

**This is an as-of backup, not infinite real-time replication.**
If the canonical source keeps writing while a tail is captured,
a newer head will require a new immutable snapshot. Changes to
the source mid-capture fail closed rather than relabeling an old
snapshot as the newest canonical head. Nothing is automatically
pushed to another device.

## Operator CLI

```bash
# First export complete sealed segments explicitly using the R0.17 CLI.
PYTHONPATH=src python -m mojum.segment_archive_cli export \
  --db /private/source.sqlite3 --agent-id dante-fixture-001 \
  --segment-size 64 --archive-dir /private/owner-only-archive \
  --expected-source-head TRUSTED_SOURCE_SHA --include-private

# Then capture the remaining incomplete tail against the SAME source head.
PYTHONPATH=src python -m mojum.segment_archive_cli capture-tail \
  --db /private/source.sqlite3 --agent-id dante-fixture-001 \
  --segment-size 64 --archive-dir /private/owner-only-archive \
  --expected-source-head TRUSTED_SOURCE_SHA --include-private

PYTHONPATH=src python -m mojum.segment_archive_cli verify-point \
  --archive-dir /private/owner-only-archive --source-events 130 \
  --expected-source-head TRUSTED_SOURCE_SHA

PYTHONPATH=src python -m mojum.segment_archive_cli restore-point \
  --archive-dir /private/owner-only-archive \
  --new-db /private/NEW-recovered.sqlite3 --source-events 130 \
  --expected-source-head TRUSTED_SOURCE_SHA
```

All CLI operations emit summary metadata only, not original PRIVATE
event payloads or source file paths. The archive contains PRIVATE
**plaintext**. HMAC from optional Aquarium replay-state caching
is not an encryption layer and is not required for cold replay.

## QA and limits

- Real 130-event SQLite: **128 sealed + 2 tail**, independent
  read-only verification and full recovery after disposable source
  is made unavailable. A 7-event genesis-only tail also restores.
- Actual v4 synthetic Aquarium: **130 motor frames + bootstrap = 131
  event records**, with 128 sealed + 3 tail, fully restored and
  deterministic cold replay of the same organism checkpoint.
- Crash injection after tail data before manifest; corrupt
  bytes, missing sealed data, wrong external trusted head, orphan
  file, absent PRIVATE opt-in, world-readable directory,
  existing destination, and repeated idempotent export.
- Real offline CLI subprocess for capture, verify and restore,
  including refusal to copy without `--include-private`.
- A separate heavy fixture writes **100001** actual PRIVATE
  synthetic Noosphere source records, **24 sealed 4096-event
  segments (98304)** plus **1697 unsealed events**, makes
  source unavailable, verifies and reconstructs the exact
  100001-event SHA-linked history from physical files.

- A second **full end-to-end Aquarium** 3.13 stress: 10,050
  actual v4 goal-free motor frames, a durable epoch event at 9999,
  more than 4,000 measured `toy_probe_v2` contacts and **10,052**
  source journal events. Archive **8,192 sealed records** plus
  **1,860 live-tail records**, make the source disposable SQLite
  unavailable and reconstruct a new database from physical files.
  A complete cold resimulation must produce an identical Aquarium
  checkpoint, epoch, sensory association counts and motor memory
  before continuation to tick 10,051.

**Important exclusions:** no automatic source replication, no
encrypted authenticated multi-device transport, no anti-rollback
guarantee without a separately trusted head, no two actual
independent devices/volumes, no live writer failover, no crash
durability claims across all Android/Windows filesystems, no
automatic backup scheduler. Physical archive tests are not
100001 real Aquarium motor ticks; the v4 sensor budget is still
finite. No full neural brain or proof about subjectivity.

Next requirements remain tracked in
[Issue #141](https://github.com/Garyu-tt/mojum/issues/141):
authenticated roots and key rotation, two physically independent
authorized destinations, incremental sealed/tail shipping and
acknowledgements, writer fencing and partition conflicts.
Keep Draft; require exact SHA CI and security review before merge.
