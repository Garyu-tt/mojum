# R0.16: append-only logically segmented Noosphere SQLite/1

**Main integration candidate, forward-ported from [PR #139](https://github.com/Garyu-tt/mojum/pull/139)**.
Its origin is the isolated R0.15 research branch, but this package is limited
strictly to the five opt-in segmented-journal paths, adapted to the current
`main` Noosphere `EMPTY_HEAD` symbol. No automatic upgrade or physical
device/prod deployment.

## What is implemented

`SegmentedNoosphere` is an explicit new backend using a DIFFERENT
SQLite schema `mojum.noosphere.segmented-sqlite/1`.
A synthetic/private caller must open a **new empty path** and supply
a fixed `segment_size` in 64..8192 (default 4096); changing that
size or using an old `mojum.noosphere.sqlite/1` file fails before
any committed event. No legacy event is truncated, moved, removed,
rewritten or rehashed as this new format.

Within one canonical SQLite file, immutable `segments` records pin:

- monotonic `segment_index`;
- absolute first journal `seq` for that segment;
- exact `prior_segment_head`, the hash of the last event
  in the previous segment, or 64 zeros at genesis; and
- versioned `mojum.noosphere.segment-start/1` schema.

Each appended event includes absolute `seq`, segment number, the
previous **global event hash**, and the checked original event
envelope in a NEW `mojum.noosphere.segmented-event-hash/1` hash
preimage. The event chain **never resets** at segment boundaries.

Segment boundary creation and its first event are committed in
the **same SQLite FULL/WAL transaction**. Multi-writer
`BEGIN IMMEDIATE` and one global SHA-256 head CAS reject stale
races even across a rollover. Bounded `append_many` admits
1..256 events per all-or-nothing transaction. `append`, `head`,
`count`, `verify_chain`, `event_at`, `iter_events` and a bounded
read-only `dataset` projection match the primary journal's API
shape, but not its persisted hash/schema format.

Verification streams all events and segment metadata without
materializing 100k source events in memory; checks noncanonical
payload, all indexed SQL field copies, event hash, timestamps,
contiguous sequence, expected segment and every parent segment
link. It also rejects a head/count change seen during verification.
`iter_segment_receipts(external_expected_head=...)` provides a
stream of **metadata-only** records (segment index, absolute first/last
sequence, prior segment hash and last event hash) after a full chain
scan. This exposes neither PRIVATE event payload nor learned state.
These receipts are *not yet authenticated*; future independent
devices can persist them as external anchors rather than trusting
a single local snapshot. The optional independently pinned expected
final hash detects whole-log rollback **only** when the anchor
remains independently trusted.

Physical **single SQLite file**, not several separate files and
not fault-tolerant replicas. An attacker who can rewrite the entire
database and external anchor can still forge a new chain. HMAC
snapshot authentication and journal integrity are distinct. This
project does **not** promise crash-proof hardware storage or
cryptographic non-repudiation.

## Boundary and actual-load QA

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_segmented_noosphere.py -v
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_segmented_noosphere_100k_stress.py -v
```

The first suite uses **actual** disposable PRIVATE SQLite files and
tests 4 x 64-event segment boundaries, CAS races between two handles,
an interrupted caller **after** a committed segment transition,
atomic rollback of a batch failing after segment creation,
manual SQL tamper detection, strict no-private-default dataset
projection, and refusal to open legacy databases.

The stress suite commits **100,001 distinct actual synthetic events**
using <=256-event transactions, physically reopens SQLite, verifies
all events and 25 interlinked logical segments with an independent
head anchor, appends event 100,002, re-verifies, and proves that
the default dataset does not emit PRIVATE payloads.

This is a data/journal scalability test, **not** 100,001 Aquarium
motor/sensory frames. The current v4 typed sensor still has
`MAX_ABSOLUTE_TICKS=99,999`, and old Aquarium explicitly
uses `LocalNoosphere` with its old 100k-event gate. The new backend
is intentionally NOT automatically injected into Aquarium.

## Non-goals and next integration gates

1. Explicitly opt-in Aquarium to the new backend with a **new
   immutable bootstrap schema**, preserving legacy v1/v2/v3/v4
   journal and HMAC replay-state compatibility and strict
   goal-free input isolation. Do not assume source hash equivalence.
2. Extend the **typed** absolute epoch/sensor/association time
   budgets under another versioned contract, and test >100k
   actual canonical Aquarium frames, not just the journal.
3. Durable independent anchor receipts, a reviewed physical
   multi-file/archive manifest and >=2 independently stored
   replicas with real crash/recovery tests. All historic segments
   must stay inspectable; a snapshot does not replace history.
4. Real Termux/Android CPU storage/suspend checks and independent
   review of failure, concurrency and security semantics.

The original stacked research PR #139 remains unmerged and is not a release target.
This isolated direct-`main` forward-port #359 is eligible for acceptance only after
successful CI on its final commit and independent code review. Merge affects
new opt-in module/test/docs paths only; it never rewrites legacy Noosphere
histories, deploys to production, or enables the backend in Aquarium.
