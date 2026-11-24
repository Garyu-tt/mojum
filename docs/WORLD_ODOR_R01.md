# World odor field R0.1

The optional `mojum.world.odor/1` module produces deterministic **synthetic marker concentration observations** over the canonical point-room geometry. It is not CFD, chemistry, an olfactory model, or a physical sensor.

## Example

```python
from mojum.world_room import Room, initial_state
from mojum.world_odor import create_profile, sample

room = Room(5.0, 5.0, ((2.0, 0.0, 0.5, 4.0),))
state = initial_state(room, 1.0, 1.0)
profile = create_profile(room, created_tick=0, decay_per_tick=2, sources=[
    {"id": "vent", "x_mm": 1000, "y_mm": 1000, "concentration": 100},
])
receipt = sample(profile, room, state, tick=0, expected_revision=state.revision,
                 points=[{"id": "nose", "x_mm": 1500, "y_mm": 1000}])
```

Coordinates and dimensions are mapped to integer millimetres. Sources decay by a fixed integer amount per logical tick. Distance attenuation uses integer one-square-metre bands; opaque axis-aligned obstacles block source-to-sample paths when occlusion is enabled. The output and source ordering are deterministic.

## Boundaries

- Explicit opt-in only; no Aquarium/GENESIS/Noosphere wiring.
- PUBLIC_SYNTHETIC fixtures and explicit consent only.
- No filesystem, network, wall clock, physical sensor, gas model, or hardware access.
- The SHA-256 digest detects accidental receipt changes; it is not a signature or authentication.
- This is an observation fixture, not an authoritative sensory or behavioral input.
