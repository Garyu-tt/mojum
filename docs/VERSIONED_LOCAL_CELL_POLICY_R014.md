# R0.14: explicit v2 local-cell motor policy, old SQLite journal compatibility

**Draft candidate. NOT a default behavior change, physical motor or organism release.**

## Why a new version?

The existing R0.13 Explorer (schema mojum.developmental.explorer/1)
records blocked outcomes in 0.5 m zones and can suppress movement in small
rooms. Changing its algorithm in place would invalidate deterministic replay
of old PRIVATE Noosphere histories. This patch adds a separately versioned
LocalCellExplorer (schema mojum.developmental.explorer.local-cell/2).

The default remains coarse_v1: legacy bootstrap manifests, journal frame
payloads and snapshot schemas are unchanged. New experiments opt in using
motor_policy=local_cell_v2 at birth. The v2 bootstrap explicitly pins both
motor_policy and motor_policy_schema; v2 event and checkpoint state have
their own schema. Reopening v1 as v2 or vice versa fails closed by matching
the persisted bootstrap, without mutation or implicit conversion.

v2 uses a 4 cm own-position cell and only current typed Proprioception:
own coordinates, tick, world revision and heartbeat. It learns from
measured blocked/unblocked motor outcomes; no goal/reward, future world,
object identity, geometry oracle, emotion or hardware motor permission.
Snapshot decoding is bounded and strict on fields, types, keys and counts.

SQLite CAS append occurs before adopting world, learner and policy RAM.
Cold restart deterministically replays source history. PRIVATE export
accepts the same explicit policy argument, so it cannot reinterpret v2
frames under a v1 model or produce a fake unrelated viewer trace.

## Reproduction (CPU, stdlib Python 3.11+)

Run the new unit tests:

    PYTHONPATH=src python -m unittest discover -s tests -p test_motor_policy_v2.py -v

Run all regression tests:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v

On a NEW, explicitly private SQLite path only:

    PYTHONPATH=src python -m mojum.aquarium --db ./new-v2.sqlite3 --ticks 120 --exploration-mode goal_free --motor-policy local_cell_v2
    PYTHONPATH=src python -m mojum.aquarium --db ./new-v2.sqlite3 --ticks 30 --exploration-mode goal_free --motor-policy local_cell_v2
    PYTHONPATH=src python -m mojum.aquarium_trace_export --db ./new-v2.sqlite3 --output ./v2.private.jsonl --exploration-mode goal_free --motor-policy local_cell_v2 --allow-private-export

Never upload that PRIVATE file to public GitHub. Default old v1 archives
continue to open without specifying any motor-policy argument.

## Acceptance

Run full CPython 3.11/3.12/3.13 Aquarium/motor/mesh CI and the actual
Chrome observer check at final head; separately verify v1 historical
replay, wrong-mode fail-closed, concurrent CAS, post-commit restart,
no partial RAM adoption, and PRIVATE v2 trace export.

The brain placeholder remains UNIMPLEMENTED; this is a synthetic point
room, not neural intelligence, MuJoCo, a physical actuator, mobile host,
encrypted distributed memory, or a claim of no subjective experience.

A real algorithm promotion still requires independent review, additional
held-out contact-generalization tests (#108) and explicit authorization.
No automatic old-journal migration, merges or closing #106/#55/#51.
