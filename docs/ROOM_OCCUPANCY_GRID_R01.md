# Offline room occupancy grid R01

Parent issue: #7. Implementation claim: #1158.

The pure-stdlib `mojum.room_occupancy_grid.rasterize_occupancy` accepts a native Python dict with exactly `schema`, `provenance`, `room`, `cell_mm`, `obstacles`. Schema is `mojum.room.occupancy/1`; provenance must be `PUBLIC_SYNTHETIC`. Rectangles contain integer millimetre coordinates `x0,y0,x1,y1`, with strictly increasing corners. Cells are indexed by row from the minimum y and column from the minimum x. A cell is occupied iff its open interior intersects the open interior of at least one obstacle. Touching edges alone does not count. Partial last cells are clipped to room bounds.

Limits: coordinate magnitude <= 1,000,000 mm; cell size 1..100,000 mm; at most 1024 obstacles and 65,536 cells. Inputs require exact native dict/list/int/str types; bool is not an integer. The result is a read-only mapping of immutable rows, occupied count, dimensions and a canonical SHA-256 consistency digest. Digest is **not** authentication.

Run: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_room_occupancy_grid.py'`.

No filesystem, process inspection, network, MuJoCo, runtime wiring, private Noosphere data, or physical actuation. This grid is a geometric approximation, not a collision-safety guarantee. Draft PR requires independent QA.
