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

## Browser motor policy lineage (review candidate)

The one-file offline observer now displays the persisted developmental
policy version directly next to measured telemetry:

- `coarse_v1` — historical half-meter zones;
- `local_cell_v2` — explicitly opted-in 4 cm cells;
- external-script mode — no learned developmental policy in the frame.

A single imported JSONL trace may **not mix** developmental policy schemas.
The real Chrome smoke checks both policy labels on desktop (1280 CSS px)
and emulated mobile (390 CSS px). It also splices v1 and v2 frame bytes
from two actual locally generated synthetic SQLite histories, recomputes
their frame SHA-256 footer, and verifies the viewer still refuses playback
on cross-policy mixing, with controls disabled.

This policy check is *consistency validation*, not origin authentication.
A file hash contained in the same untrusted file is not a signature.
No private journal is sent to the browser over a network or to CI as an
artifact; synthetic local frames only are used for the smoke test.

## Full canonical, not just isolated controller, regression

`tests/test_motor_policy_v2_canonical_integration.py` uses the actual
R0.14 Aquarium with two distinct SQLite files to run v1 and v2 through
a contact-rich 16 cm room, comparing successful motion and longest
rest streak instead of rewarding zero collisions. New v2 is also tested
as 60 committed ticks + cold restart + 60 ticks against an uninterrupted
120-tick control organism with identical heartbeat, source revisions,
policy state and final journal head. Wrong-policy reopen and wrong-policy
PRIVATE export must refuse before creating a public export; the original
SQLite history remains untouched. Legacy v1 history continues to replay
after the additional v2 implementation is installed.

These are deterministic synthetic tests, not a claim that 4 cm is the
optimal grid, that a neural brain is present, or that every environment
has accessible movement.
