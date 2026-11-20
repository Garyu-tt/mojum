# Mojum P0 #55: bounded goal-free movement affordance memory (pre-integration)

**Experimental CPU-only module; this is not yet the canonical Aquarium loop.**

This narrow R0.13 development slice makes **one future synthetic movement
decision depend on previous measured world feedback**, instead of solely on
a scripted `dx,dy` or an assigned target coordinate.

## Numeric interface

- `AffordanceMemory.choose(sensor, pulse, enabled=True)` accepts only a
  validated synthetic near-field numeric cue, virtual tick, previous world
  revision, and boolean heartbeat pulse. It returns a bounded virtual-only
  displacement (`x+`, `x-`, `y+`, `y-`, or disabled `rest`).
- A deterministic, initially untrained policy rotates ties and maintains a
  small empirical table keyed by observed numeric RGB/distance/pulse.
  Earlier unblocked movements are tentatively repeated; known blocked ones
  lose preference to other local actions. This is learned movement feasibility
  in a synthetic room, **not a goal, reward-learning benchmark or emergent
  intelligence**.
- After a real `world_room.advance` transition, the caller may pass the
  strictly shaped outcome to `record(sensor, pulse, intent, outcome)`.
  It rejects stale timestamps, extra spectator/truth fields, invalid commands,
  forged action parameters and impossible learning snapshots.
- Memory is bounded to 128 numeric contexts and 3999 logical frame ticks.
  Snapshots contain only evidence totals and no hidden object names, future
  paths, material softness, user preferences or cognitive claims.
- `AffordanceMemory.from_snapshot(snapshot)` validates and copies state.
  A consuming runtime must **clone before calculating, persist with
  LocalNoosphere's CAS append, then adopt the clone after SQLite commit**.
  This module does not itself own SQLite; direct `record` mutates a
  candidate in memory only.

## Deterministic two-environment demonstration

```bash
PYTHONPATH=src python examples/affordance_witness.py
PYTHONPATH=src python -m unittest discover -s tests \
  -p test_goal_free_affordance.py -v
```

Two freshly initialized learners receive the same numeric near-field
observation and heartbeat. Both initially choose `x+`. In a clear synthetic
room, `world_room.advance` reports movement allowed. In the other room,
a real swept-AABB obstacle blocks the *same* proposed movement. At the next
tick, with identical current numeric sensor context, the first tentatively
repeats `x+`, the second tries `x-`. This is a deliberately small
**causal experience-dependent action witness**, not a claim of a learned
personality or general sensorimotor understanding. The learner never receives
the hidden room obstacle coordinates; only its own result's blocked flag.
A fabricated `blocked` outcome can still deceive it, so the eventual
Aquarium adapter must independently recompute the outcome using the trusted
world and authoritative revision barrier.

## Strict boundaries and next integration gate

- No modification to `src/mojum/aquarium.py`, its existing schema/journal,
  the active installed-package PR, or the main branch. An opt-in canonical
  `developmental_fixture` mode is **future work** under issue #55.
- No movement leaves the synthetic `world_room` fixture. The physical
  `MotorGate` is not accessed or modified; no emergency-stop state is read
  or reset. `rest` explicitly emits `dx=dy=0`.
- No camera, MuJoCo, external device, network, Node-RED, GPU, body joints or
  pretrained object identity; no machine-learning generalization metric.
- The real environment can be more complex than an RGB/range/pulse summary.
  This memory can conflate different places sharing numeric cues; only local
  empirical evidence is tracked. Do not promote it as safe exploration around
  humans or actuators.
- **Issue #55 acceptance remains OPEN**. A subsequent reviewed PR must
  integrate a typed sensory→decision→measured-outcome→learning flow into a
  single Aquarium's SQLite event journal; prove CAS failure atomicity,
  deterministic replay/restart, revision checks and a frozen-policy control,
  then pass independent QA. Standalone tests do not meet that milestone.

No PRIVATE Noosphere history or production addresses are included.
