# World spawn grid R0.1 (synthetic, offline)

`mojum.world_spawn.candidates` enumerates collision-safe point-room starting
coordinates for deterministic fixture testing under [world #7](https://github.com/Garyu-tt/mojum/issues/7).
It is **not** a live entity spawner, path planner, VR/game adapter, MuJoCo solver,
or a claim of a functioning organism. It never opens a journal, filesystem,
network, physical device or clock.

```python
from mojum.world_room import Room, initial_state
from mojum.world_spawn import candidates

room = Room(5, 5, ((2, 2, 1, 1),))
for p in candidates(room, columns=5, rows=5, clearance=0.1, limit=8):
    state = initial_state(room, p.x, p.y)
    print(state.x, state.y, state.revision)
```

Contract:

- Grid cells are sampled at their centers, sorted by integer squared distance
  from the *grid* center, with row/column tie breaks. This is deterministic
  under a pinned Python runtime and geometry, not a random distribution.
- Rectangles use the same closed AABB obstacle contact as `world_room`.
  `clearance` is an explicit **L-infinity** (axis-aligned) exclusion distance,
  including the inflated obstacle boundary. Wall clearance is inclusive.
- Inputs reject booleans, non-finite numbers, malformed grid/limit values and
  altered invalid room dataclasses. A detached `Room` snapshot is validated.
- At most 128 columns, 128 rows, 16,384 cells, 250,000 potential
  cell-obstacle checks, 1,024 returned points; exceeding the budget fails
  closed with `ValueError` before iteration. Rounded duplicate coordinates
  are not returned twice.
- Empty output is **not** proof of no free space. A grid can miss narrow free
  regions; larger resolution or a different placement algorithm may be needed.
- No revision/state is forged: use `initial_state(room, x, y)` to obtain the
  canonical checked state and SHA-256 revision, and do not reuse coordinates
  against a different geometry snapshot.

Run: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_spawn.py' -v`.
