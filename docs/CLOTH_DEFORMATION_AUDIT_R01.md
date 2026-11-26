# Cloth deformation audit R0.1

## Purpose

`mojum.cloth.deformation-audit/1` is a bounded, offline, read-only geometric
measurement for paired synthetic cloth meshes. Given rest vertices, current
vertices and triangle indices, it measures how supplied geometry differs from
its rest shape. It complements (but does not replace) the cloth topology audit,
garment manifest, importer, LOD planner, benchmark comparator and contact
telemetry work.

The module is CPU-first and uses only the Python standard library. It does not
load GLB, invoke MuJoCo/Jolt/PBD, simulate motion, calculate contact forces,
infer unobserved collisions, alter an organism, or write a journal.

## Input contract

Call `audit_deformation(rest_vertices, current_vertices, triangles)`.

- Both vertex arrays are list/tuple collections of 3..4096 XYZ triples.
- Coordinates are finite native Python int/float values in metres, within
  +/-10,000 m. Booleans and numeric subclasses are rejected.
- Both arrays must have identical vertex counts and retain the same vertex
  ordering/topology.
- Triangle indices are list/tuple triples of distinct native integers in range;
  1..8192 triangles are admitted. Duplicate faces are rejected regardless of
  winding.
- `visibility` must be exactly `PUBLIC_SYNTHETIC`. No private assets or
  journal data are accepted.

The rest mesh must have non-degenerate edges (at least 1e-9 m) and triangle
areas (at least 1e-18 m^2). The current mesh may contain collapsed faces; these
are counted and included with zero area ratio rather than hidden by dropping
samples.

## Measurements

Each undirected edge is measured once. Its stretch ratio is
`current_length / rest_length`; 1.0 means unchanged, values above 1.0 stretched,
and values below 1.0 compressed. The report includes min, median, nearest-rank
p95 and max ratios, plus p95/max absolute strain (`abs(ratio - 1)`).

Each triangle's area ratio is `current_area / rest_area`. The same min/median/
p95/max summaries are reported. Collapsed current triangles are counted.
Orientation reversals are counted when a current triangle normal has a negative
dot product with its rest normal. This is a geometric warning only: a large
fold can rotate a face normal by more than 90 degrees, so the count is not
proof of physical inversion.

A SHA-256 digest binds the exact normalized input coordinates, triangle order
and visibility. The report includes aggregate metrics and the digest, never
the source vertex arrays. Percentiles use nearest-rank selection
(`ceil(p * n)`, one-based).

## Limits and interpretation

- Bounds: 4096 vertices, 8192 triangles, at most 24576 unique triangle edges.
- Runtime/memory scale linearly with admitted geometry plus sorting measured
  edges/areas.
- Identical mesh ordering is required; topology changes need a new mesh identity
  and a separate topology audit.
- This is not FEM/XPBD strain energy, material stress, collision detection,
  self-collision, cloth/body coupling, or a solver benchmark.
- Ratios from different mesh resolutions or different input identity must not
  be compared as if they were the same physical workload.
- No automatic quality threshold or solver winner is declared. Callers define
  acceptance tolerances for their own verified workload.

## Example

```python
from mojum.cloth_deformation import audit_deformation

rest = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
current = ((0, 0, 0), (1.1, 0, 0), (1.1, 1, 0), (0, 1, 0))
faces = ((0, 1, 2), (0, 2, 3))
report = audit_deformation(rest, current, faces)
print(report.edge_abs_strain_max, report.area_ratio_p95)
```
