# Cloth seam-path admission, r01

Module: \`mojum.cloth_seams\`
Version: \`mojum.cloth-seams/1\`

## Purpose

This opt-in, CPU-only utility checks whether an explicit sewing path is geometrically admissible between two disconnected triangle-mesh panels. It is a pre-solver admission check, not a sewing-pattern generator or a physical cloth simulation.

The caller must explicitly assert \`provenance="PUBLIC_SYNTHETIC"\`. This is an input contract, not cryptographic provenance verification.

## Input

- \`vertices\`: native list of 3D xyz coordinates in metres (3..4096).
- \`triangles\`: native list of three-index faces (1..8192).
- \`seams\`: native list of at most 256 exact dictionaries with \`id\` and \`pairs\`.
- Each seam has at least two ordered stitch pairs: \`[[a0, b0], [a1, b1], ...]\`.
- Each side of a seam must be a continuous path along boundary edges. Each pair must join distinct connected mesh components.
- \`max_gap_m\`: explicit geometric tolerance, from 0 through 0.25 m (default 0.01 m).

The validator rejects non-native primitive/container types, seam records with any cardinality other than two fields before enumerating their keys, malformed or duplicate faces, degenerate triangles, unreferenced vertices, non-manifold edges, non-boundary endpoints, component changes, reused stitch vertices, discontinuous paths, excessive gaps and out-of-budget inputs.

## Output and reproducibility

Returns frozen \`SeamValidation\` and \`SeamSummary\` dataclasses. Reports contain counts, per-seam stitch count and gap aggregates, and SHA-256 over canonical UTF-8 JSON input plus schema/provenance/tolerance. Original geometry is not retained in the report. Summary ordering is lexical by seam ID.

Run focused tests:

\`\`\`sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_cloth_seams.py' -v
\`\`\`

## Limits and non-claims

- No solver, force, material, collision, deformation, self-collision, drape or dynamic behavior is computed.
- Boundary adjacency is derived from triangle edge incidence. This does not prove garment pattern correctness, panel orientation, seam allowance, watertightness after stitching, or physically valid contact.
- Euclidean pair gap is a simple admission metric, not a stitch trajectory or material stretch model.
- The caller owns asset provenance and coordinate units. Only caller-supplied in-memory data is examined; no filesystem, network, database, GLB, Noosphere PRIVATE journal, device or Aquarium runtime access occurs.
- Stdlib only; no mandatory dependencies and no automatic integration with wardrobe or physics APIs.
