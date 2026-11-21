# Mojum R0.13: staged opt-in point-room motor-play fixture

This is a **small goal-free motor exploration milestone**, not a neural brain,
a conscious organism, MuJoCo, real motor control, or a release of full P0 #55.
The existing six-node Graph IR still reports brain `UNIMPLEMENTED`.

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

## Limitations and acceptance boundary

No goal, subjective experience, neural motor learning, real proprioception,
anatomy, 3D contacts, MuJoCo, cryptographic Noosphere replication, or true
environment understanding is claimed. The implementation is a deterministic
tabular point-room baseline that demonstrates a **causal measured outcome can
change a future autonomous action**. Journal replay remains bounded to
10,000 events. P0 #55 stays open for independently reviewed causal
sensorimotor/GENESIS integration.
