# World odor field R0.1

Status: opt-in synthetic fixture; not a physical or biological sensor.

## Scope

`mojum.world.odor-field/1` samples caller-supplied points in the deterministic
point-room. Inputs are explicitly classified `PUBLIC_SYNTHETIC` and require
`consent=True`. This module does not perform CFD, chemistry, fluid transport,
biological olfaction, persistence, network exchange, or runtime integration.
It must not be treated as an authoritative behavior input.

## API

- `OdorSource(source_id, x_mm, y_mm, concentration, created_tick=0, decay_numerator=1, decay_denominator=1)`
- `SamplePoint(point_id, x_mm, y_mm)`
- `sample_odor_field(room, state, sources, points, *, consent, falloff_mm=1000, occlusion=True)`

Coordinates are integer millimetres in the room's metre coordinate system.
Concentrations are dimensionless fixture units, not ppm or physical units.
Each source contributes a softened inverse-square-like attenuation:
`floor(C * r² / (r² + d²))`. Logical-tick decay applies the source's rational
factor once per tick using deterministic fixed-point integer arithmetic.
Contributions are summed and saturated at 1,000,000,000.

When enabled, occlusion uses the point-room's closed-AABB segment intersection.
Touching an obstacle boundary counts as blocked. This is a geometric shadow
approximation, not airflow simulation. Room state revision is recomputed and
must match before sampling.

## Safety and bounds

- Python standard library only; no I/O, filesystem, network, wall clock or subprocess.
- At most 512 sources, 4,096 points and 100,000 source-point pairs.
- Obstacle membership and occlusion work is capped at 1,000,000 geometry tests per call.
- Exact native type checks reject bool-as-int and dataclass subclasses.
- Duplicate IDs, malformed IDs, invalid geometry, stale room revisions,
  future source ticks, excessive values and malformed decay ratios are rejected.
- Input collections and result records are detached/immutable at the API boundary.
- Stable ID ordering and canonical JSON SHA-256 receipt make repeated equivalent
  inputs reproducible. The digest is a consistency receipt, not authentication.

## Non-goals

No real gas concentrations, diffusion/advection, temperature/humidity coupling,
particle transport, sensor noise, chemistry, persistence, private Noosphere
records, Aquarium integration, hardware access, or physical-device claims.
