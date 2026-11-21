# R0.15 — explicit versioned epoch continuation (v4)

Status: experimental, opt-in CPU-only implementation on a new local SQLite
journal. This is a bounded continuity slice, not a claim that Mojum is an
endless autonomous digital organism.

## What is implemented

`--motor-mode local_cell_v4` introduces three new, non-interchangeable wire
contracts:

- `mojum.developmental.proprioception/2` carries `absolute_tick`,
  `epoch_index` and `epoch_tick`. `absolute_tick` is monotonic and is never
  modulo-wrapped into the old `/1` tick field.
- `mojum.developmental.local-cell/4` retains the deterministic 512-cell LRU
  policy with explicit eviction and forgotten-observation accounting.
- `mojum.aquarium.epoch-transition/1` is a durable PRIVATE journal event.
  At absolute ticks 9,999, 19,998, ... it is committed before the first
  motor frame in that epoch. Frame IDs continue as `frame-000000009999`, not
  from zero.

The v4 manifest pins the epoch-marker and proprioception schemas. Its
checkpoint uses `/2` and records the epoch index/local tick. The read-only
`mojum.aquarium_wire` inspector recognizes and validates both epoch markers
and v4 frames.

## Failure and replay contract

The marker and frame are separate CAS commits. If the process stops after the
marker but before the frame, the source journal contains one pending boundary;
reopening derives the same epoch index and appends exactly one first frame.
The RAM policy is adopted only after that frame commit. A stale or wrong-policy
reopen fails before appending, and old v1/v2/v3 journals retain their original
selectors, schemas and 9,999-tick behavior.

The implementation is intentionally conservative: v4 does not accept the
legacy toy-contact bridge, does not silently extend the old sensory model, and
does not add a new cloud service or device dependency.

## Reproduce

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./v4_PRIVATE.sqlite3 \
  --motor-mode local_cell_v4 \
  --neural-mode disabled \
  --ticks 120

PYTHONPATH=src python -m unittest \
  tests.test_aquarium_epoch_v4 -v

PYTHONPATH=src python -m mojum.aquarium_wire \
  --db ./v4_PRIVATE.sqlite3
```

The test uses disposable synthetic SQLite only and exercises 9,999 committed
frames, an injected post-marker/pre-frame failure, cold replay, and a resume
through tick 10,050. It does not print PRIVATE event payloads.

## Explicit limits and remaining gates

- `absolute_tick` is bounded below 99,999; the journal is bounded at 100,000
  events. Epoch markers consume events, so the event ceiling is reached before
  the theoretical absolute-tick ceiling.
- The journal remains a local hash chain with an independently supplied head,
  not an HMAC-authenticated snapshot or an encrypted/anti-rollback record.
- No physical motor, Android background guarantee, MuJoCo body, camera,
  consciousness, subjective experience, or generalization claim follows from
  this synthetic replay.
- A separately reviewed authenticated segmented journal and authenticated
  replay-cache integration are still required before any long-running or
  distributed continuity release.
