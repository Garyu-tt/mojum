# World range sensor R0.1 (issue #7)

This is an **opt-in, read-only, deterministic, stdlib-only** sensor for the existing 2D point-room fixture. It is deliberately not a physics engine, real-world sensor, actuator, Aquarium default policy, or contact event.

~~~python
from mojum.world_room import Room, initial_state
from mojum.world_range_sensor import read_range_frame

room = Room(4, 4, ((2, 1, 0.1, 2),))
state = initial_state(room, 1, 2)
frame = read_range_frame(room, state, expected_revision=state.revision, max_range_m=5)
assert frame["rays"][0]["direction"] == "east"
assert frame["rays"][0]["distance_m"] == 1
assert frame["physical_contact"] is False
~~~

## Contract

- Schema: 'mojum.world-range-frame/1'. A read measures the **supplied tick only**. No new step, wall-clock read, network/file IO, authority transfer, or mutation of Room/RoomState.
- Geometry: four rays in fixed order **east (+x), north (+y), west (-x), south (-y)** from the point location. The nearest closed obstacle rectangle or room border is measured. A ray tangent to an obstacle edge counts as a hit, even for a 1 mm rectangle. Equal-distance obstacles resolve to the lowest original obstacle index.
- Range: finite numeric scalar strictly greater than 0 and at most 1000 m (default 10 m). A hit **at** the limit is reported as a hit. Beyond it: 'distance_m=max_range_m', 'hit=range_limit', 'obstacle_index=null', never a fabricated obstacle.
- Evidence: 'expected_revision' must match both supplied state revision and the room/tick/position-derived revision. Invalid/stale positions and revisions fail closed. 'frame_sha256' is a deterministic **unkeyed checksum only**, not an authenticity signature.
- Frame: 'tick', 'room_revision', 'position', 'max_range_m', 'rays', 'provenance', 'physical_contact=false'. Each ray: 'direction', 'distance_m', 'hit' (obstacle/boundary/range_limit), 'obstacle_index' (int only for obstacle hits).
- CPU: constant four ray directions, O(obstacle count) scans, at most 4096 rectangles (the existing Room limit), no third-party dependencies. Python 3.11+.

## Boundaries

This is **PUBLIC synthetic geometry only**. It does not read or write PRIVATE Noosphere or Aquarium journals; does not grant external VR/world capabilities or control devices; and does not make physical-contact claims. It does not overlap the separately claimed issue #7 world trace replay or the existing Aquarium learned four-ray proximity pipeline. Integration with runtime or organism learning is out of scope and must pass a separate versioned adapter and QA gate.

## Test

~~~bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_range_sensor.py' -v
~~~
