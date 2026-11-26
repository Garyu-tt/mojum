# Offline point-room sampled route witness (R01)

Parent: #7. Standalone opt-in planning/QA probe, not part of organism inference or Aquarium bootstrap.

`mojum.world_reachability.find_sampled_path(room, start, goal, columns=16, rows=16)`
tries direct travel, then a deterministic row-major grid of free cell centers with
4-neighbor moves and local start/goal links. Segments honor the canonical
point-room *closed* AABB collision, wall and per-tick displacement rules.

- **`witness_found`**: ordered `path` contains virtual ticks, positions and
  revision hashes. Every step has been replayed with canonical
  `world_room.advance(..., expected_revision=...)`; consumers can recheck with
  `verify_witness(room, report)`. A geometrically valid path exists **within
  the synthetic point-room fixture**, not necessarily a safe actuator route.
- **`inconclusive`**: no witness was found at this grid resolution. **Never**
  interpret as `unreachable`. Narrow gaps and sparse sampling can cause false
  negatives. This tool cannot prove global room connectivity.
- Input points (2 finite numeric values) are normalized to floats; room
  geometry retains its existing exact numeric spellings. `room` must be a
  valid `Room`, start/goal must be free. Reports do not mutate any world state.
- Caps: at most 32 cells per axis and 1024 cells total, 128 obstacles, 10 km
  room spans, and the fixture's 1000 m per-tick movement budget. Oversized
  audits raise `ValueError`. Search never imports Gymnasium or MuJoCo.
- No network, filesystem, wall clock, SQLite, Noosphere, secrets, actual
  hardware, or nondeterministic random inputs. Nothing is written or
  automatically exposed to an agent policy. Reports are **not signed** and
  do not authenticate their author. A valid replay authenticates only
  consistency with the supplied room and the returned canonical revision.

Minimal invocation:

```python
from mojum.world_room import Room
from mojum.world_reachability import find_sampled_path, verify_witness
room = Room(6, 6, ((2.5, 1.5, 1, 3),))
report = find_sampled_path(room, (1, 3), (5, 3), columns=12, rows=12)
if report["status"] == "witness_found":
    assert verify_witness(room, report)
```

Tests: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_reachability.py' -v`.
Keep branch as Draft until independent exact-head CI and review. Other #7 workers own
trace replay (#808), range sensor (#812), and canonical runtime integration.
