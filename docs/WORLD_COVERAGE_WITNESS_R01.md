# Offline point-room coverage witness R01 (#926, parent #7)

This is an **optional, stdlib-only, read-only, PUBLIC_SYNTHETIC** movement audit.
It replays explicit movement commands using `mojum.world_room.advance` and its
per-tick revision check. It does not mutate runtime Aquarium state or read any
SQLite/PRIVATE Noosphere history. It is **not** MuJoCo physics, real-world sensor
coverage, proof of accessible free space, cryptographic authentication or a
learning/goal/reward model.

## Minimal fixture

```json
{
  "schema":"mojum.world-coverage-input/1",
  "classification":"PUBLIC_SYNTHETIC",
  "room":{"width":4,"height":4,"obstacles":[[2,1,0.1,2]]},
  "start":[1,2],
  "cell_size_m":1,
  "steps":[{"dx":0.5,"dy":0},{"dx":1,"dy":0},{"dx":0,"dy":0},{"dx":-0.5,"dy":0}]
}
```

Run from an installed checkout: `python -m mojum.world_coverage_witness fixture.json`
(or call `witness(dict)` directly). The CLI reads at most 512 KiB + 1 byte,
rejects duplicate JSON keys, unknown fields, nonnative JSON numerical types,
NaN/Infinity, JSON nesting beyond 16 levels, unbounded room/grid/steps
and non-public classifications.
On failure it exits 2 with a generic error, without echoing source data.

## Precise report semantics

- `ticks`: requested replay ticks. `blocked_steps`: canonical AABB/wall
  blocked outcomes. `moving_steps`: unblocked steps with changed coordinates.
  `no_progress_steps`: unblocked commands without coordinate change (including
  zero commands and too-small sub-ULP movement). Their sum equals `ticks`.
- `distance_m`: sum of actual coordinate deltas, not requested displacements.
- `visited_cells`: sorted integer grid coordinates for start + **sampled tick
  endpoints only**. Cells crossed between endpoints are **not** inferred;
  coverage is **not** proof that cells are reachable or obstacle-free.
- `nominal_grid_cells`: rectangular bounding grid cells *including blocked
  obstacle area*. Never interpret visited/nominal as free-space coverage.
- `start_revision` / `final_revision`: actual point-room SHA revisions;
  `trace_sha256`: deterministic local hash of the canonical fixture and ordered
  outcomes. It is not signed; a hostile party can recompute it.
- `physical_contact=false`: no physical hardware or real MuJoCo claim.

Budgets: max 4096 steps, 128 rectangles, 1000 m room axis / displacement
component, 256 grid columns and rows (65,536 nominal cells). 4097 max
reported visited positions. All counts stay bounded and testable in CPU-only
Python 3.11+. The module does not automatically load input from the network.

## Acceptance

`PYTHONPATH=src python -m unittest discover -s tests -p test_world_coverage_witness.py -v`

Independent QA and exact-head Aquarium/Motor/Mesh CI required before merging
into protected `main`.
