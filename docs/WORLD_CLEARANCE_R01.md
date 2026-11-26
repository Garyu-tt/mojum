# Synthetic point-room nearest-surface clearance R0.1

Tracking: issue #956, parent world simulator #7. This opt-in, offline, stdlib-only sensor **does not** change the authoritative point-room collision solver, movement, Noosphere, real sensors, or motor control. It returns a JSON-compatible dictionary detached from input geometry, not a physics/contact sample.

## Example

```python
from mojum.world_room import Room, initial_state
from mojum.world_clearance import clearance_frame
room = Room(20, 20, ((8, 8, 1, 1),))
state = initial_state(room, 6, 6)
frame = clearance_frame(room, state, max_range_m=5)
assert frame["nearest"] == {"kind": "obstacle", "index": 0}
assert frame["distance_m"] == 2**1.5
```

`mojum.world.point-room-clearance/1` records the canonical room-state SHA-256 revision and logical tick, the finite `max_range_m`, a finite nonnegative `distance_m`, `saturated`, an optional nearest surface, the synthetic provenance, and `physical_contact=false` / `actuating=false`. The `digest_sha256` is SHA-256 over sorted-key, compact ASCII JSON of all other fields: useful as a reproducibility checksum **only**, never an authentication/MAC.

The distance is the Euclidean minimum from a **point**, not from a body radius. Room walls are closed and are ordered **west, east, south, north** for deterministic exact ties. For obstacles, each closed rectangle is measured as `hypot(max(left-x, 0, x-right), max(bottom-y, 0, y-top))`; original obstacle input order breaks exact ties. Walls win ties against obstacles. An obstacle boundary is *not* an admissible position in the canonical point-room fixture, whereas outer wall contact is allowed and reports zero clearance.

For finite positive range, a true clearance **strictly greater** than `max_range_m` reports `distance_m=max_range_m`, `saturated=true`, and `nearest=null`, preventing an out-of-range sensor from revealing which obstacle was closest. A reading at exactly the cap remains unsaturated. Distance is computed on all obstacles, so output is deterministic independent of available range. Bounds: 1,000,000 m maximum width, height and requested range, 4,096 source obstacles, 64-bit nonnegative logical ticks. Inputs are strictly typed (`bool` and nonfinite numerics rejected), all room geometry is revalidated, and the stored revision must exactly match canonical `world_room._revision` on the validated room/tick/position. Oversized or mismatched input raises `ClearanceError` before producing a frame.

No continuous collision detection or path feasibility guarantee is implied: `world_room.advance` remains the authority for full swept movements. No external device reads, network calls, persistence, external libraries, private data or clock access.

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_clearance.py' -v
```

The dedicated regression covers wall and obstacle selection, diagonal Euclidean distance, equal ties, exact range behavior, state revision tampering, geometry mutation, strict finite bounds, and non-mutating read-only operation. This is a new additive draft feature; independent exact-head CI and review are required before main integration.
