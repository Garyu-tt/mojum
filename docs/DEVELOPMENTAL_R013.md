# Mojum R0.13: staged opt-in point-room motor-play fixture

This is a **small goal-free motor exploration milestone**, not a neural brain,
a conscious organism, MuJoCo, real motor control, or a release of full P0 #55.
The existing six-node Graph IR still reports brain `UNIMPLEMENTED`.
The motor integration was accepted into `main` via PR #202; this follow-up documents and tests it.

## Run on a single CPU, offline

From repository root (Python 3.11+):

```sh
PYTHONPATH=src python -m mojum.aquarium --db ./motor_play_PRIVATE.sqlite3 --ticks 120 --motor-mode developmental_fixture
PYTHONPATH=src python -m mojum.aquarium --db ./motor_play_PRIVATE.sqlite3 --ticks 10 --motor-mode developmental_fixture
PYTHONPATH=src python -m unittest discover -s tests -v
```

A fresh local DB records one bootstrap plus one PRIVATE frame per tick. The
second command cold-replays and continues the *same* organism, with exactly
the same learned policy predictions. This is a checksum-based SQLite record,
not encrypted storage, portable security proof or distributed checkpoint.

## Explicit sensory-action contract

The opt-in policy initially samples four safe point-room cardinal motions
(0.125 room units), then chooses based on Laplace-smoothed empirical
success/block counts. Periodic exploration and rest avoid a fixed destination
or external reward. It receives only its own **committed prior action/outcome**
history, never the room geometry, object IDs, rewards, target coordinates,
future measurements or spectator-only annotations.

The simulation computes the proposed policy/world/neural state speculatively,
appends one frame with SQLite compare-and-append, then adopts it in memory.
Cold replay ignores recorded actions as commands: it recalculates the policy
from the previous committed history and checks the complete frame. A failed
append must not advance the policy, world or virtual tick.

Default `--motor-mode scripted` retains old bootstrap and frame shape.
The developmental mode is pinned in the bootstrap and rejects externally
provided `--dx`/`--dy` or `Aquarium.step(dx=..., dy=...)`.
Every recorded developmental action is only a virtual 2D point-room
displacement: the physical actuator/torque interface is never invoked.

## Read-only mobility audit (blocked-count blindness)

Collision counts alone cannot distinguish useful exploration from a policy
that has stopped moving or keeps re-entering the same two positions. The
audit re-reads a committed PRIVATE journal through a read-only SQLite handle,
verifies the event chain, and reports measured mobility:

```sh
PYTHONPATH=src python -m mojum.motor_mobility --db ./motor_play_PRIVATE.sqlite3
```

The report includes frame/action/blocked/successful counts, idle fraction,
longest rest streak, distinct visited positions, coverage span, path length
and a versioned gate result (`rest_collapse`, `revisit_concentration`). It
also accepts `--expected-head` for an independently recorded chain head.

Thresholds are conservative heuristics, not a navigation score: a rest streak
of at least `max(10, frames // 10)` combined with an idle fraction of at least
0.5 marks `REST_COLLAPSE_SUSPECTED`; at least 20 moves with at least four
successful moves per distinct position marks
`REVISIT_CONCENTRATION_SUSPECTED`. Histories with no measurable displacement
report `NO_MEASURED_DISPLACEMENT` instead of the misleading `MOBILITY_OBSERVED`,
even when attempts occurred; one frame cannot establish movement on its own.
Both gates read only committed frames; the
tool never imports the runtime, never writes to the journal, and does not
reinterpret existing policy semantics. A scripted journal, a non-developmental
bootstrap or a noncanonical frame is rejected instead of being averaged.

## Limitations and acceptance boundary

No goal, subjective experience, neural motor learning, real proprioception,
anatomy, 3D contacts, MuJoCo, cryptographic Noosphere replication, or true
environment understanding is claimed. The implementation is a deterministic
tabular point-room baseline that demonstrates a **causal measured outcome can
change a future autonomous action**. Journal replay is bounded and streams in
bounded batches up to the 100,000-event ceiling. P0 #55 stays open for
independently reviewed causal
sensorimotor/GENESIS integration.
