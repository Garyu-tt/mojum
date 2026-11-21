# Mojum R0.14 — opt-in version-pinned local-cell Aquarium

**Experimental direct SQLite Aquarium integration, stacked on #113→#109→
#104→#95. Draft only; no change to production or main.**

## Explicit new mode

An ordinary existing R0.13 Aquarium uses the unchanged default
`developmental_policy="legacy"`. Its original
`mojum.developmental.explorer/1` schema and exact bootstrap manifest
fields are preserved. Existing PRIVATE SQLite journals can still be
reopened with the *same* original policy and deterministic frame replay.

A NEW, separate empty PRIVATE SQLite journal can be created with:

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db /path/to/NEW-private-localcell.sqlite3 \
  --exploration-mode goal_free --developmental-policy local_cell_v2 \
  --ticks 120
```

This chooses `CellExplorer` using current proprioception and synthetic
heartbeat only; no obstacle geometry, action look-ahead or reward.
Every committed frame pins `mojum.developmental.local-cell/2`.
The bootstrap manifest additionally pins
`"developmental_policy":"local_cell_v2"`. 4 cm cell semantics are
part of the strict, snapshot-validated policy implementation. All
SQLite events remain PRIVATE with the existing unchanged CAS/hash-chain
commit and deterministic full replay machinery.

## Cross-version refusal / fail-closed migration

- An old journal opened as `local_cell_v2` must fail with a bootstrap
  mismatch **before writing new frames**. A new journal opened as legacy
  likewise fails; no silent semantic reinterpretation or rewriting.
- Bad policy values or `local_cell_v2` with scripted mode reject before
  SQLite is created. External movement remains forbidden in goal-free
  exploration.
- Existing old journal headers and checkpoint field shape are unchanged.
  The *new* checkpoint's `developmental_state` has strict v2 schema,
  and each v2 frame includes v2 schema and causal policy digest. Payload
  tampering is caught by existing event verification/recompute.
- There is deliberately **no automatic conversion** of an individual
  Dante/Noosphere journal. A future authorized migration requires a
  separate copy-and-verify strategy with an explicit immutable legacy
  provenance record, never mutating the original private history.
- The separate #101 signed snapshot cache and portable #97 Termux host
  are not in this stacked branch; they must explicitly understand v2
  or reject it. This draft does not modify either PR or promise they work.

## Reproduction

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_aquarium_cell_policy_v2.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

This verifies actual PRIVATE SQLite creation, 120 frames, fully verified
restart, a 121st append, wrong-policy refusal, immutable old replay,
lossless policy snapshot validation and precise match with the original
research local-cell arm.

The brain remains `UNIMPLEMENTED`, synthetic room is not MuJoCo, and no
hardware actuation, distress/pain/valence or reproduction was enabled.
No claim of sentience, proof of non-suffering or general-world cognition
is implied by this simple motor-response policy.

## Review gates

The stress distribution in #113 was deliberately selected after viewing
prior baseline, and the independent new-scene generalization gate remains
open. v2 must not merge automatically; require exact-final-head CI,
cross-version replay QA and independent security/replay review. Future
schema changes require their own explicit versions, not reinterpretation
under `/2`.
