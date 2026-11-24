# Cloth scheduling policy R01

Status: opt-in scheduling contract; not a cloth solver and not a CPU-time guarantee.

## Contract
The versioned schema moj um.cloth.schedule/1 is a pure-stdlib, solver-neutral decision function. The caller supplies a monotonically increasing logical tick and estimated work counts. At most one update decision is returned for that tick.

| Profile | Tick divisor | Max vertices | Max constraints | Catch-up |
|---|---:|---:|---:|---:|
| OFF | 1 | 0 | 0 | 0 |
| LOW | 4 | 2,000 | 8,000 | 1 |
| MEDIUM | 2 | 8,000 | 32,000 | 1 |
| HIGH | 1 | 32,000 | 128,000 | 2 |

These are conservative starting ceilings, not measured performance claims. CUSTOM profiles may be constructed within hard validation ceilings.

## Determinism and overload
- Scheduling depends only on policy, logical tick and explicit arguments.
- No wall-clock sampling, randomization or hidden adaptive state.
- Non-due ticks return cadence; disabled feature/policy returns disabled.
- Work above the profile ceiling is rejected.
- Backlog beyond the catch-up limit is reported as dropped. At most one update is planned per call; backlog is never converted into a larger physics timestep.
- The caller retains authority over body/world dt, motor safety and journal order.

## Non-claims
This does not run MuJoCo/Jolt/PBD, simulate fabric, estimate CPU milliseconds, guarantee realtime deadlines, generate LOD meshes, persist snapshots or integrate with Aquarium. Vertex/constraint ceilings are caller-supplied admission metadata, not proof of solver memory or runtime cost. OFF remains the default until a real solver adapter is separately implemented and validated.

## Verification
PYTHONPATH=src python -m unittest tests.test_cloth_schedule -v

Python 3.11+, standard library only, offline.
