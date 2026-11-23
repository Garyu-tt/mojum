# Synthetic cloth topology admission (R0.1)

Issue #376. This is an **optional, offline, CPU-stdlib audit**, not a GLB importer,
a cloth solver, a mesh repair operation, an apparel attachment API or a physics
collision guarantee. It does not alter `Aquarium`, MuJoCo, body, safety gates or
canonical PRIVATE journals. No external dependencies, files, network or process
execution. Input must already be trusted-to-parse in-memory synthetic geometry.

```python
from mojum.cloth_topology import audit_cloth_mesh
report = audit_cloth_mesh(
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
    [(0, 1, 2), (0, 2, 3)],
)
assert report.boundary_loop_count == 1
```

Admission limits: 3–4,096 unique vertices; 1–8,192 triangles; three finite,
strictly numeric xyz coordinates per vertex, each within ±10,000 metres.
Float and int (not bool) are accepted; negative zero canonicalizes to positive
zero. Indices must be distinct exact integers, in range. Any triangle with
squared doubled-area ≤1e-24 square-metres-squared is rejected. All vertices
must be used. Identical positions require welding first. Any edge shared by
more than two triangles, reversed/duplicate faces, inconsistent face winding,
vertex bow-tie/disconnected 1-ring, branched boundary or isolated vertex is
rejected. Valid manifold surfaces can have open boundaries, including multiple
holes. All panels must be connected by default; `require_connected=False`
allows disjoint individually manifold panels and reports their count.

Result includes deterministic geometry SHA-256, vertex/triangle/edge counts,
boundary edges and loops, and connected components. Vertex order, face order,
and winding are part of the digest. Hashing is **not authentication** and the
report proves neither physical thickness nor self-intersection freedom,
UV/skin mapping, stitchability, contact correctness, collision safety, nor
motor readiness. Real mesh inspection and runtime coupling require separate
reviewed integrations. No user assets may be ingested by this module directly.

Testing from repo root: `PYTHONPATH=src python -m unittest discover -s tests -p 'test_cloth_topology.py' -v`.
Acceptance requires an independent review, exact-head CI and NO implicit merge.
