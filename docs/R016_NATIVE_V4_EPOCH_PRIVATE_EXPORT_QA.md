# R0.16: Native PRIVATE v4+sensory-v2 segmented SQLite to offline JSONL

Status: **Draft research integration only**, stacked on PR #140, which
is stacked on PR #139 and PR #136. It does not change parent branches
or `main`, read or rewrite personal user histories, or deploy a real
phone/physical body.

## Why this is different from a synthetic browser fixture

PR #137 used a QA serializer to demonstrate real 9999→10000 epoch
frames. This work updates the actual supported application
`mojum.aquarium_trace_export`, which already implements replay-verified
local JSONL export, rather than teaching a one-off test to pretend it
is the shipping exporter.

An explicit operator request is mandatory:
- `allow_private_export=True` (CLI: `--allow-private-export`);
- v4 `developmental_policy="local_cell_v4"`;
- choose `journal_backend="legacy"` or `"segmented"` explicitly;
- segmented journals require exact `journal_segment_size`;
- provide an independently captured `expected_journal_head` for v4;
- explicitly name the observation mode `sensory_mode="none"` or
  `sensory_mode="toy_probe_v2"`, using the exact original room/scene
  when not the defaults.

On each export the source file must be an existing regular SQLite
file. Read-only SQLite Backup API staging creates a disposable local
snapshot; the original remains unchanged, including live WAL history.
A *separate real Aquarium instance* fully verifies the snapshot's
canonical source owner/bootstrap, global/segment chain/head, causal
motor and sensory observations, and epoch boundaries before any
JSONL bytes are published. This is not based on trusting JSON alone.

The exporter handles both original `events` and new
`segmented_events` tables via explicitly selected backend and
does not guess/migrate based on their names. Existing legacy
exports retain their /1 footer contract; only v4 gets
`mojum.aquarium.checkpoint/2` and versioned, metadata-only fields:

- `policy_schema = mojum.developmental.local-cell/4`;
- `epoch_index` / `epoch_tick` from the *actual checkpoint*;
- `epoch_boundaries` extracted from **committed and replay-verified**
  `aquarium.epoch` events whose absolute ticks are inside the
  requested displayed slice;
- existing `trace_range`, `trace_integrity` for exact LF-terminated
  UTF-8 JSONL frame bytes, and original full journal head/count.

It never places `developmental_state`, `toy_contact_state`, neural
weights, genome revision, hidden toy identity or learned-model
snapshots in the footer. Private *frames* are still PRIVATE: there
must be a separate, explicit permission decision to disclose them.

The same 5000-frame/24MiB/64000-character budgets apply; full
histories above that limit fail rather than truncate silently.
`start_tick` and `frame_limit` must both be supplied for slices.
The destination must be a *new* file in a trusted directory; atomic
publication is no-clobber with POSIX mode 0600. On any replay/ABI/
head/budget failure no destination is published. The source database
is never repaired, partially migrated, or written.

## Example: segment around the first epoch transition

Assuming the synthetic database was originally created with the
matching default genome, graph, room, and scene:

```bash
PYTHONPATH=src python -m mojum.aquarium_trace_export \
  --db /absolute/path/to/synthetic-segmented.sqlite3 \
  --output /private/new-epoch-slice.jsonl \
  --allow-private-export --expected-head <independently-saved-sha256> \
  --developmental-policy local_cell_v4 \
  --journal-backend segmented --journal-segment-size 4096 \
  --exploration-mode goal_free --sensory-mode toy_probe_v2 \
  --start-tick 9998 --frame-limit 5
```

For a custom synthetic room or ToyScene, use
`export_private_trace(..., room=..., sensory_scene=...)` with exact
original typed values; the CLI does not recreate arbitrary scenes
from a synthetic journal's bootstrap.

## Executed-gate definition

- CPython 3.11/3.12/3.13 tests using **real** disposable SQLite:
  legacy backend v4, segmented v4, six-frame sensory-v2 case, wrong
  independent head, wrong owner/schema/segment size, no export opt-in,
  existing output no-clobber and original source bytes unchanged.
- Dedicated GitHub Actions test with **10,003 actual synthetic motor
  frames, >4,000 measured sensory v2 contacts, one epoch boundary at
  absolute tick 9999 and 10,005 segmented events**. Full cold SQLite
  replay and independent original head validation. The native exporter
  selects only five committed frames 9998..10002; then immutable
  PR #137 reviewer viewer SHA 7752f510 runs in REAL desktop/mobile
  Chrome, verifies both epoch labels, frame SHA and two deliberately
  rewritten negative fixtures with recomputed hashes.

This is cross-branch QA only, not an independently signed export.
The frame checksum is inside the editable JSONL file; it detects
accidental byte corruption but **cannot prove genuine origin** against
someone able to rewrite both the data and hash. A captured journal
head also needs separate trusted storage to prevent total rollback.

## Explicit limits

- The new segmented backend is still **one physical SQLite file**,
  not multiple independent replicas or distributed durability.
- Current v4 sensor/clock remains limited to 99,999 motor ticks
  even if the journal supports up to 262,144 events.
- This does not demonstrate Android background operation,
  a real sensory/brain model, MuJoCo or any subjective state.
- Independent review and owner approval are required before merge;
  original PR #116 incompatible writable v2 ABI gate stays red.
- PR #140's other releases and source ancestors still require
  independent acceptance; do not auto-promote them because QA passes.


## Additional R0.16 parent compatibility regression discovered

While checking #140's exact baseline, the original Aquarium and v4
epoch integration suites were **RED** (runs 44855942810 and
44855942775). Three existing budget tests monkeypatched the legacy
`MAX_REPLAY_EVENTS` downward, but #140 captured a static
`self.journal_limit` on constructor and then used that cache
for admission, replay and portable-host preflight. The tests reported
`AssertionError: ...Error not raised`. This is a real legacy safety
compatibility defect, not merely an obsolete test.

The daughter branch implements `Aquarium._effective_journal_limit()`
and uses it for append, pre-epoch admission and cold-replay bounds.
Legacy mode uses the **minimum** of original selected bound and the
current fail-closed legacy `MAX_REPLAY_EVENTS`, including dynamically
tightened budget settings. The explicit segmented source continues
to use its own separate journal limit; the older constant must not
silently defeat the opted-in higher backend capacity. The portable
host uses both the actual Aquarium effective limit and its own
legacy preflight limit. No policy/motor/sensor/SQLite wire schema
changes.

Dedicated `test_r016_legacy_budget_compat.py` covers: already-open
legacy writer refuses a newly tightened journal limit without appending;
cold replay refuses an already over-limit source; portable host refuses
the entire session before any step; and segmented v4 can still run
under artificial tiny legacy test constants. The original preexisting
`test_streaming_journal_replay.py`,
`test_portable_host.py` and `test_epoch_v4.py` regressions must also
return green on the exact child HEAD before any acceptance.

This compatibility change does not assert that owner-controlled
configuration can safely modify quotas in production at runtime;
it restores the prior testable fail-closed behavior and preserves
the default legacy format and separate segmented source.
