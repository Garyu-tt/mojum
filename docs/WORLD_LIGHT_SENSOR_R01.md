# World Light Sensor R0.1

## Status

Experimental, opt-in, offline CPU fixture. This module is not connected to
Aquarium, GENESIS, Noosphere, the canonical world runner, or any device.

## Contract

```python
from mojum.world_room import Room, initial_state
from mojum.world_light_sensor import measure_illumination

room = Room(10.0, 10.0, ((4.0, 4.0, 1.0, 2.0),))
state = initial_state(room, 1.0, 5.0)
frame = measure_illumination(
    room,
    state,
    tick=state.tick,
    expected_revision=state.revision,
    sources=(("ceiling-lamp", 9.0, 5.0, 0.8),),
    ambient=0.05,
)
print(frame.total, frame.occluded_sources)
```


Each explicit source is a tuple of (source_id, x, y, intensity), with a unique
1-32 character ASCII identifier. At most 16 sources are accepted. Source and
sensor positions must be inside the room and outside closed obstacle AABBs.
The state tick and room revision must match exactly; the revision is recomputed
using the existing canonical world-room revision function before measurement.

The fixture estimates contribution as:

    intensity / (1 + squared_distance)

A straight source-to-sensor segment intersecting or tangent to a closed obstacle
is occluded. Ambient, summed direct contribution, and final total are all
bounded to [0, 1]. Output is detached and immutable, with visible/occluded
source IDs, normalized contributions and a canonical SHA-256 digest.

## Meaning and limits

- Values are normalized fixture units, **not lux** or calibrated irradiance.
- Point sources and inverse-square attenuation are a deliberately small model.
- Obstacles are opaque 2D axis-aligned rectangles; no diffraction, reflections,
  volumetric scattering, material transparency, spectral response, or 3D geometry.
- The digest is a reproducibility/corruption witness, not authentication.
- The sensor does not prove physical contact, real-world illumination, or
  perception. Its frame always declares physical_contact=false.
- No file, network, clock, microphone, camera, hardware, or journal access.
- Python standard library only; no change to existing room physics or policy.

## Verification

Run the focused suite:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_light_sensor.py' -v
```

