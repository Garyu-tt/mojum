# R0.14 — opt-in Local Cell v3 bounded retention

Draft, stacked on [v2 fault QA #117](https://github.com/Garyu-tt/mojum/pull/117),
which depends on #115→#113→#109→#104→#95. No direct-main
changes or auto-conversion of stored synthetic organism history.

## The availability problem

Experimental v2 remembers blocked actions separately for every visited 4 cm
proprioceptive cell, up to 512 distinct cells. A 513th new cell causes an
explicit exception, preventing further progress. This was proven by
the disposable, bounded unit protocol in #117 and recorded in
[issue #120](https://github.com/Garyu-tt/mojum/issues/120).

## New versioned policy, unchanged earlier policies

`developmental_policy=local_cell_v3` is accepted only with goal-free
exploration. The default `legacy` policy remains untouched and existing
`local_cell_v2` journals still use **exactly** the v2 schema and
causal replay semantics.

A new, distinct PRIVATE SQLite journal's bootstrap records:

- `developmental_schema: mojum.developmental.local-cell/3`
- `developmental_policy: local_cell_v3`

Every newly appended v3 frame pins
`mojum.developmental.local-cell.outcome/3`, the measured contact
outcome and an explicit retention event when necessary. Opening the same
database as v1/v2 must fail closed **before appending any new event**.

The control policy itself uses only current proprioception, pulse and
already measured outcome. It cannot read room geometry, objective scores,
future ticks, filesystem state or private Noosphere. No distress/pain
representation is introduced.

## Deterministic memory cap

- Exactly **512 live cell records maximum**, each with the same
  coarse action-count evidence as v2.
- On the **513th distinct cell**, evict the least-recently-*observed*
  cell. Ties break by the exact cell-string key, so replay on different
  supported Python versions is deterministic.
- On a revisited cell, its last-seen tick is refreshed. No eviction
  occurs until a new cell is seen with the budget exhausted.
- Snapshot pins per-cell last-seen tick, total evictions and
  *forgotten observations*. The strict accounting invariant is:
  `retained_observations + forgotten_observations == total_updates`.
  Zero, negative, future, boolean, inconsistent and alien-schema
  counters fail snapshot validation; caller objects are copied.
- Forgetting means explicit **bounded data retention**, not a reward,
  punishment or affective aversion mechanism. An evicted cell can later
  be rediscovered without memorized earlier blocked evidence.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_aquarium_cell_policy_v3.py -v

PYTHONPATH=src python -m mojum.aquarium \
  --db /tmp/NEW-SYNTHETIC-v3.sqlite3 \
  --exploration-mode goal_free \
  --developmental-policy local_cell_v3 --ticks 120
```

The 2400-cell stress sequence is a **constructed unit protocol** with
different injected current proprioceptive positions; it is NOT a
physically plausible 2400-step room trajectory, navigation benchmark
or proof of contact-based learning. Separate actual synthetic
point-room tests validate exact action equivalence with v2 until
eviction and 600-tick PRIVATE SQLite cold replay/resume.

## Important remaining availability and security limits

This fixes the **512-cell local RAM ceiling** only. The canonical
`Proprioception` still enforces ticks below 9999 and this Aquarium
still budgets 10,000 journal events, so it is not a genuinely endless
organism. A future versioned epoch rollover + replay compaction/
streaming design (separate #98 effort) and robust peer failover
are required before any always-on claim.

The SQLite history is hash-chained but **not automatically
authenticated** against a filesystem attacker. v3 is a synthetic
policy, not a neural model, MuJoCo body or physical actuator. No
inference about subjective experience/suffering is warranted.

Keep this PR Draft, require exact-final-head CI, independent code/
journal review, Android/CPU footprint QA, and an untuned independent
room set before production integration.
