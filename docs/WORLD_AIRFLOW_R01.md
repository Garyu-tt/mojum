# Synthetic point-room airflow sensor R0.1

The module implements **mojum.world.airflow/1**, an opt-in, read-only analytic airflow cue for the existing deterministic 2D point-room fixture. It composes explicitly supplied source vectors; it does **not** solve fluid dynamics, infer real air movement, or alter an organism's body or motor commands.

## API

~~~python
from mojum.world_airflow import sample_room_airflow

sample = sample_room_airflow(
    room, room_state,
    tick=room_state.tick,
    expected_revision=room_state.revision,
    x_mm=1500, y_mm=1200,
    sources=[{
        "id": "vent-east",
        "x_mm": 8000, "y_mm": 1200,
        "vx_mm_s": -2500, "vy_mm_s": 0,
    }],
    public_synthetic=True,
)
~~~

Each source is a native mapping with exactly `id`, `x_mm`, `y_mm`, `vx_mm_s`, and `vy_mm_s`. Coordinates and velocity are integer millimetres and millimetres/second. Room dimensions and obstacle geometry must align exactly to the millimetre grid. At most 32 sources are admitted; each vector component is limited to 20 m/s. A source's influence uses a deterministic inverse-square-style weight `R² / (R² + d²)` with `R = 10 m`. Components are accumulated with integer rounding, and combined Euclidean output above 100 m/s is rejected by an exact squared-magnitude comparison (the reported integer magnitude is rounded down only after admission). A closed obstacle intersecting the source-to-sample segment fully occludes that source.

## Safety and replay boundaries

- Explicit `public_synthetic=True` is mandatory. No PRIVATE memory or production data is accepted.
- `tick` and `expected_revision` must match the canonical current `RoomState`; stale or corrupted states fail closed.
- No file/network access, wall clock, external dependencies, hidden environment input, persistent writes, physical actuation, or Aquarium runtime wiring.
- The digest is a deterministic consistency digest, not a signature or proof of source truth.
- Obstacles use a closed-boundary 2D segment test; this is a deliberately simple line-of-sight approximation, not airflow around obstacles or pressure simulation.
- A zero-source or fully occluded request returns a zero vector. Source order does not affect the result.

## Local test

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_airflow.py' -v
~~~
