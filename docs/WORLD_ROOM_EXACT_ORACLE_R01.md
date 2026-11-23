# World room exact-rational collision oracle (R0.1)

**Issue:** #829, child QA slice of world simulator #7. **Scope:** opt-in tests, not a new movement engine. Public synthetic inputs only. No production/private journals, hardware, external data, network, model downloads or new runtime dependencies.

## Independent property

`src/mojum/world_room.py` uses floating-point Liang-Barsky clipping against closed axis-aligned obstacle rectangles. `tests/test_world_room_exact_oracle.py` independently intersects the attempted segment with each of four rectangle edges using signed 2-D orientation (cross products) over `fractions.Fraction`. Endpoint-inside checks and exact collinear/on-edge tests cover inclusive boundaries; outer room bounds are checked separately. This intentionally **does not call** the production `_segment_hits_rect` helper, duplicate Liang-Barsky, or use sampled points.

The deterministic corpus uses **integer-valued** widths, heights, obstacles, starts and displacement vectors, so exact arithmetic and production floats have no ambiguous epsilon contracts in this range. This is a correctness cross-check for the **synthetic point room**, not measured MuJoCo physics, full continuous collision detection with curved bodies or arbitrary-precision float accuracy.

## Reproduce

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_room_exact_oracle.py' -v
```

Four regression methods cover 320 seeded rooms × 5 moves (1,600 move cases, replayed twice), deterministic revision chain fingerprint, skinny obstacles, corner/edge grazing, near misses, room wall reachability, obstacle-order invariance and stale/invalid tick rejection. Corpus seed: `0xC01115A`. The SHA-256 of serialized move traces is pinned in the test to catch inadvertent fixture/replay drift. Generated room size is at most 80×80 integer units, at most four rectangles per scene and 20-unit coordinate displacement per move; every move fits the point-room's 1,000-unit cap. Pure Python stdlib, no I/O.

The oracle has limited fault-detection scope: symmetric bugs outside the bounded integer input space, invalid room manifests, float precision near machine epsilon, live MuJoCo, external actions and long-run Noosphere replay require separate acceptance. A green result is **not** approval to merge other pending world/physics branches.
