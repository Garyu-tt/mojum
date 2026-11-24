# World surface observation map R0.1

**Status:** opt-in, synthetic-only metadata contract. It is not a contact solver.

## Contract

`mojum.world.surface/1` maps integer millimetre coordinates to a descriptive
material observation. A map is pinned to one 64-character lowercase SHA-256
room revision and bounded room dimensions. Each rectangle carries a stable
patch/material token, integer milli-friction and milli-restitution estimates,
and an integer priority.

Rectangles are half-open: `[x, x + width) × [y, y + height)`. Overlaps are
resolved by descending priority, then ascending patch ID. No match yields the
explicit synthetic default (`material_id=default`, friction 0.500,
restitution 0.000). Input ordering does not affect the map digest.

## Example

```python
from mojum.world_surface import SurfaceMap, SurfacePatch, observe_surface

room_revision = "a" * 64  # replace with the caller's pinned room revision
surface = SurfaceMap(
    room_revision, 5000, 4000,
    (SurfacePatch("floor-wood", "wood", 0, 0, 5000, 4000, 650, 40),),
)
observation = observe_surface(
    surface, x_mm=1200, y_mm=800, tick=12,
    expected_room_revision=room_revision,
)
```

## Limits and safety

- CPU/stdlib only; no runtime wiring or mandatory dependency.
- Inputs are PUBLIC_SYNTHETIC fixture data. Do not place private journal or
  user asset data in source control or CI.
- The module reports metadata only. It does not change movement, collisions,
  friction impulses, restitution, motor policy, or MuJoCo state.
- Coefficients are caller-provided estimates, not measured/calibrated values.
- No wall clock, filesystem, network, device, or hardware access.
- Exact-head CI and independent review are required before merge.
