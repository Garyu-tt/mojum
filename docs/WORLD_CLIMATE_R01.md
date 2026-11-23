# Synthetic room climate sensor R0.1

## Contract

`mojum.world_climate.sample_room_climate` is an explicit, read-only sampler for
the existing canonical 2D `Room` / `RoomState` fixture. The caller supplies
all climate parameters and must explicitly label the input
`PUBLIC_SYNTHETIC`. The result is a frozen `ClimateFrame` tied to the exact
room-state revision and logical tick.

Temperature is represented as integer milli-degrees Celsius. The field is
`base + gx*x + gy*y`, where gradients are milli-degrees per metre. Each
coordinate product is rounded to the nearest integer, with exact half ties
away from zero, using the exact integer ratio of the admitted Python number.
Relative humidity is a caller-supplied uniform integer in permille (0..1000).

## Bounds and validation

- Base and sampled temperature: -100,000..100,000 milli-degrees C.
- Each spatial gradient: -100,000..100,000 milli-degrees C per metre.
- Relative humidity: 0..1000 permille.
- Tick: native integer in the non-negative signed 64-bit range.
- Exact canonical `Room` and `RoomState` types, valid unobstructed position,
  and matching canonical room revision are required.
- Booleans, integer subclasses, omitted/forged provenance, stale revisions,
  malformed metadata, unsupported numeric ranges and non-native parameters
  fail closed with `ClimateError`.

## Example

```python
from mojum.world_room import Room, initial_state
from mojum.world_climate import PUBLIC_SYNTHETIC, sample_room_climate

room = Room(width=8.0, height=6.0)
state = initial_state(room, 2.0, 3.0)
frame = sample_room_climate(
    room,
    state,
    base_temperature_millic=21_000,
    gradient_x_millic_per_m=250,
    gradient_y_millic_per_m=-100,
    humidity_permille=550,
    provenance=PUBLIC_SYNTHETIC,
)
print(frame.temperature_millic, frame.humidity_permille)
```

## Reproducible test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_climate.py' -v
```

## Explicit non-goals

This is a synthetic, caller-defined affine field. It does not simulate heat
transfer, airflow, moisture transport, condensation, thermal mass, occupants,
real sensors or calibrated indoor climate. It does not modify a room, state,
Aquarium, SQLite journal, Noosphere, sensorimotor runtime, wall clock, network
or device. It makes no physical or biological claim and is not enabled by
default.
