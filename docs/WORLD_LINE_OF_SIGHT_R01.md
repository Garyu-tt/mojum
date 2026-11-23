# R0.1 point-room target line of sight witness (issue #948)

This independent, optional CPU/stdlib-only slice belongs to world issue #7. It is a **read-only synthetic geometry witness**, not an optical sensor, navigational planner, camera, physical-contact report, or authorization gate. The existing `world_range_sensor` checks four cardinal rays; this API checks up to 64 explicitly named arbitrary targets from one canonical point-room state.

## Example (with `PYTHONPATH=src`)

```python
from mojum.world_room import Room, initial_state
from mojum.world_line_of_sight import read_target_visibility

room = Room(10, 10, ((4, 3, 1, 4),))
state = initial_state(room, 2, 5)
frame = read_target_visibility(
    room, state, [('behind_wall', 8, 5), ('same_place', 2, 5)],
    expected_tick=state.tick,
    expected_revision=state.revision,
    visibility='PUBLIC_SYNTHETIC',
    opt_in=True,
)
assert [target['visible'] for target in frame['targets']] == [False, True]
```

`read_target_visibility` neither advances `RoomState` nor changes `Room`/`Noosphere`. The caller provides exactly a `Room`, a `RoomState` and an ordered list/tuple of `(id, x, y)` triples. IDs are unique lowercase ASCII labels of up to 40 characters, beginning with a letter. Targets on or within obstacles are admitted but reported `visible=False`. Out-of-room, nonfinite, bool, duplicate, malformed and over-budget inputs fail as a whole with `ValueError`. Observer position must be a valid canonical point-room position.

Output schema: `mojum.world.line-of-sight/1`. Every output frame contains the public synthetic classification, stable target order, state tick and full room revision, observer/target coordinates, Euclidean distances, `visible`, `blocking_obstacle_index`, provenance, `physical_contact=False`, and SHA-256 over canonical JSON excluding `frame_sha256`. `blocking_obstacle_index` is **the first intersected obstacle in source tuple order**, not necessarily the nearest physical occluder. Intersections count **closed** axis-aligned rectangles including tangent edges/corners.

## Limits and trust boundary

- Explicit `opt_in=True` and exact native-string caller-declared `visibility='PUBLIC_SYNTHETIC'` only; validate the native type before comparing labels so hostile comparison operators cannot run. This is not verified authorization or PRIVATE provenance attestation.
- Room width/height at most 10,000 m each; at most 256 obstacles; 1..64 targets; per-target observer distance at most 1,000 m; tick integer 0..10,000,000. No file/network/device access.
- Rejects stale/forged revision and tick, malformed post-construction room/state mutation and invalid geometry before returning anything. The authoritative revision is recomputed by canonical `world_room._revision`; swept AABB contact uses canonical `world_room._segment_hits_rect`.
- This uses **point-sample 2D geometry only**: no field of view, gaze direction, opacity, object size, height, occlusion stacking, optical sensing, camera pose or 3D MuJoCo ray cast. A clear geometric ray does not prove a creature observed or recognized a target.
- The SHA-256 is an **unkeyed replay fingerprint, NOT a signature/MAC**. No PRIVATE SQLite state, live device state, external world, or real creature/participant data is read or exported.
- Floating point follows the canonical point-room fixture's numerical semantics. No promise of exact-rational or cross-architecture bitwise equivalence near floating-point degeneracies.

## Validation

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_world_line_of_sight.py -v
python -m compileall -q src/mojum/world_line_of_sight.py tests/test_world_line_of_sight.py
```

17 offline regression tests cover clear/blocked, thin obstacles, corner touches, near-misses, boundary endpoints, multiple targets, post-advance state, revisions/ticks, opt-in/privacy, forged mutated records, resource limits, hash replay and fail-closed malformed inputs. Tested locally using a source-compatible copy of `main` `world_room.py`; full integration matrix and exact PR HEAD GitHub Actions require independent verification before merge. New files only; `main` remains unmodified.
