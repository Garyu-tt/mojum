# Cloth self-intersection AABB audit R0.1

## Purpose
Optional stdlib-only broadphase candidate generator for non-adjacent triangles in a synthetic cloth-proxy mesh.

## Contract
- Input vertices are xyz triples in metres; faces are triples of exact integer indices.
- Provenance must be PUBLIC_SYNTHETIC.
- Limits: 2,048 vertices, 4,096 triangles, absolute coordinate 1,000,000 m, and 200,000 possible pair checks.
- Pairs sharing any vertex are omitted as topologically adjacent.
- AABB overlap is inclusive and output order follows input face order.
- SHA-256 fields are deterministic consistency fingerprints, not signatures or authentication.

## Run
```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_cloth_self_intersection.py' -v
```

## Explicit limitations
A candidate means only that two non-adjacent triangle axis-aligned bounding boxes overlap. It is not proof that the triangles intersect; broadphase intentionally permits false positives. This has no narrowphase test, continuous collision detection, thickness, contact response, cloth solver, calibrated geometry, or physical-contact evidence. It does not load GLB, access files/network/SQLite/Noosphere, change runtime state, or control hardware. No dependencies are added.
