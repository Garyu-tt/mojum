# Offline point-room occupancy raster R0.1

Issue #7's **isolated authoring/QA preview**. This is not an organism sensor, planner,
physics solver, canonical observation, motor permission, or PRIVATE journal frame.
The real point-room retains swept AABB collision semantics in `mojum.world_room`.

```python
from mojum.world_room import Room
from mojum.world_occupancy import rasterize, verify_raster

room = Room(4, 2, ((1.49, 0.2, 0.001, 0.6),))
preview = rasterize(room, columns=4, rows=2)
assert verify_raster(room, preview)
print(preview["blocked_rows"])  # bottom-up; see row_order
```

The JSON-serializable `mojum.world.point-room-occupancy/1` envelope includes
`geometry_sha256` derived from the **whole** immutable room geometry, grid
resolution (`columns`, `rows`), bottom-up `blocked_rows` of `#` / `.`, explicit
`row_order`, and QA-only provenance. This hash is a drift check, **not** an
HMAC, authenticated source of truth, privacy boundary, or anti-rollback proof.
`verify_raster(room, saved)` recomputes all cells and fails closed on a changed
room, changed bitmap, unknown keys or malformed grid dimensions. JSON values
preserve canonical `Room` numeric spelling; no reinterpreting the Aquarium
journal/policy schemas.

A cell is conservatively **blocked** whenever its *closed* area intersects
any *closed* obstacle AABB. Touching a face/corner counts. The outer room
boundary itself is legal for a point inside the room, but moving beyond it is
not; the occupancy grid cannot authorize motion. Very thin walls that miss
center sampling still occupy their intersecting cells. Coordinates are not
sampled from a running organism and geometry must never bypass capability-
scoped world-to-sensor adapters into GENESIS inputs.

Bounds: 1–128 cells per axis, at most 16,384 cells and 4096 canonical
`Room` obstacles. Coordinates that cannot represent a strictly increasing
cell boundary at a requested resolution are rejected, not silently aliased.
Memory is bounded by O(cells + rows + columns) plus the caller's already
validated room and serialized result; no third-party libraries or network.
For each obstacle the rasterizer marks only its conservatively intersecting
cell rectangle, avoiding an O(cells × obstacles) full scan.

Run: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_occupancy.py' -v`.
