# Cloth triangle intersection narrowphase R0.1

`mojum.cloth_triangle_intersection` is an opt-in, standard-library-only geometric audit for explicit candidate triangle pairs from a PUBLIC_SYNTHETIC cloth proxy mesh.

## Contract
- Caller supplies bounded vertices, triangle indices and candidate triangle-index pairs.
- Shared-vertex pairs are treated as adjacent and skipped.
- Non-adjacent pairs are tested in 3D; non-coplanar edge/triangle crossings and coplanar projected overlap/touch are detected.
- Boundary contact counts as intersection. Degenerate faces, duplicate candidate pairs, hostile Python types, non-finite/out-of-range coordinates and over-budget inputs fail closed.
- Output is immutable, sorted and detached, with a SHA-256 consistency digest.

## Limits
This is a narrowphase geometry helper, not a broadphase, deformable-body solver, continuous collision detector, contact manifold, friction model or proof of physical stability. Floating-point predicates use a fixed small tolerance; near-degenerate configurations may need a robust-predicate backend before production use. Candidate generation and resource budgeting remain caller responsibilities. No runtime, GLB, filesystem, network, private data or motor integration.
