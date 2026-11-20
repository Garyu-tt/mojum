# R0.14 — numeric four-ray sensing within the canonical Aquarium

**Experimental opt-in synthetic mode:** `goal_free_rays`, stacked on
R0.13 #59 → input-compatibility QA #61. This is **not** a new creature, a
second journal, a physical sensor, MuJoCo, camera vision or a release-ready
mobile agent. The original Aquarium's `scripted` and `goal_free` modes
retain their existing event contracts.

## One-command demonstration

```bash
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante_rays.sqlite3 --ticks 120 \
  --exploration-mode goal_free_rays
PYTHONPATH=src python -m mojum.aquarium \
  --db ./dante_rays.sqlite3 --ticks 30 \
  --exploration-mode goal_free_rays
```

Both invocations open the **same** SQLite Noosphere journal. The persisted
bootstrap declares `exploration_mode=goal_free_rays` and
`developmental_schema=mojum.developmental.room-ray-affordance/1`. Attempting
to reopen it with another mode, seed, room or graph is rejected. For 120
ticks the private journal contains 121 events including bootstrap; after the
next 30 ticks it contains 151 events.

## What the learner receives

At each virtual tick, the synthetic world computes the nearest axis-aligned
boundary/obstacle distance in four directions: **east, west, north, south**.
Distances are **integer 5 cm buckets capped at 60 cm** (`0..12`). The
learner sees the four integers, its current numeric x/y proprioception,
heartbeat pulse, tick and current world revision. It never receives a room
rectangle, obstruction coordinates, toy ID, material softness, future frame,
goal point, physical contact or MuJoCo state.

`RangeExplorer` learns a small versioned context table of actually
measured unblocked vs blocked movement. At birth it uses deterministic
rotating 4 cm directions and rest. Observed success can be preferred to a
novel action; an observed blocked action loses to alternatives. Different
sensor contexts have different bounded empirical counts. This is **not**
semantic cognition, motor anatomy, probabilistic planning or a neural
network learning weights.

## Causal negative control

`tests/test_aquarium_room_rays_r014.py` constructs **two actual point-room
worlds** with different hidden obstacle distances, but the **same
quantized four-ray observation** (both show east bucket `0` from the
starting state). At tick zero the synthetic heartbeat pulse rotates the innate preference,
so both newborn learners choose `x-`. The swept AABB collision engine
reports one blocked move and one successful move. When tested with an
*identical subsequent numeric input*, the one with measured success again
chooses `x-`, while the one with measured blockage selects another
direction (`y+` in the controlled tick-one input). An otherwise
blank/frozen learner also selects `y+` by innate rotation. The difference
from the frozen control is therefore due to recorded
experience, not a different clock phase or a hidden wall coordinate.

This is a **controlled synthetic causal witness**. It does not establish
learning that generalizes to real perception or arbitrary 3D worlds.
Quantization can alias distinct room situations; learned preferences can
still be wrong.

## Transaction and privacy boundaries

The canonical Aquarium calculates sensor, movement, outcome and model
changes speculatively, commits a **single PRIVATE SQLite frame using CAS**,
then adopts RAM state. Replay reconstructs the exact same sensor readings,
policy choice, blocked outcome and updated snapshot. Wrong modes and
untrusted explicit movement arguments fail closed. The new CLI rejects
forbidden overrides before creating a new SQLite file.

No external endpoint, GPU, Node.js, camera, real actuation or network call is
required. The existing MotorGate cannot be bypassed by the numeric fixture:
these commands have **only** synthetic point-room capability, and no
physical actuator is connected.

## Validation

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_aquarium_room_rays_r014.py -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Separate reviewer acceptance and full final-head CI on Python 3.11–3.13
are mandatory. No external Pixel 9/Android, manual GUI, disconnected-device
or two-node durability check is claimed. P0 #55 remains open; the next step
is actual private contact/probe experience reuse from #33/#56 instead of
recreating those implementations.
