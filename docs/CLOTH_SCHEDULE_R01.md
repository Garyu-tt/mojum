# Cloth scheduling policy R01

Status: opt-in scheduling contract; not a cloth solver and not a CPU-time guarantee.

## Contract
The versioned schema `mojum.cloth.schedule/1` is a pure-stdlib, solver-neutral decision function. The caller supplies a monotonically increasing logical tick, pending fixed-dt update count and estimated work counts. A plan admits zero or more distinct updates, but never changes the authoritative physics timestep.

| Profile | Tick divisor | Max vertices | Max constraints | Catch-up |
|---|---:|---:|---:|---:|
| OFF | 1 | 0 | 0 | 0 |
| LOW | 4 | 2,000 | 8,000 | 1 |
| MEDIUM | 2 | 8,000 | 32,000 | 1 |
| HIGH | 1 | 32,000 | 128,000 | 2 |

These are conservative starting ceilings, not measured performance claims. CUSTOM profiles may be constructed within hard validation ceilings. An enabled policy must admit at least one catch-up step; zero capacity is reserved for disabled policies.

## Determinism and overload
- Scheduling depends only on policy, logical tick and explicit arguments.
- Policy objects are reconstructed and validated at every public use; frozen dataclasses are not treated as tamper-proof.
- No wall-clock sampling, randomization or hidden adaptive state.
- Non-due ticks and disabled policies admit zero steps and report all pending steps as deferred, not dropped.
- Work above the profile ceiling is rejected and pending steps remain deferred for caller policy.
- On a due, enabled tick, `admitted_steps = min(pending_steps, max_catch_up_steps)`; every remaining pending step is explicitly counted in `dropped_pending_steps`.
- The plan reports admitted, deferred and dropped work separately. The caller executes each admitted step with its own fixed authoritative dt; the count is not permission to enlarge dt.
- The caller retains authority over body/world dt, motor safety, backlog persistence and journal order.

## Non-claims
This does not run MuJoCo/Jolt/PBD, simulate fabric, estimate CPU milliseconds, guarantee realtime deadlines, generate LOD meshes, persist snapshots or integrate with Aquarium. Vertex/constraint ceilings are caller-supplied admission metadata, not proof of solver memory or runtime cost. OFF remains the default until a real solver adapter is separately implemented and validated.

## Verification
`PYTHONPATH=src python -m unittest discover -s tests -p 'test_cloth_schedule.py' -v`

Python 3.11+, standard library only, offline.
