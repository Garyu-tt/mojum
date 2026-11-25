# Synthetic point-room temperature field R0.1

The versioned PUBLIC_SYNTHETIC temperature fixture is an opt-in geometric
observation contract. It is not thermodynamics, CFD, heat transfer, a
calibrated room model, a real sensor, or a biological thermoreceptor.

## API

Call `mojum.world_temperature.observe_temperature` with explicit
`consent=True`, a baseline in milli-degrees Celsius, a nonnegative logical
tick, 1–64 source records and 1–256 caller-supplied sample points. Coordinates
are integer millimetres in a bounded 0–100 m square. No points are generated
implicitly.

Each source provides `id`, `x_mm`, `y_mm`, `delta_milli_c`,
`decay_milli_c_per_tick`, and `observed_tick`. Each point provides `id`,
`x_mm`, and `y_mm`. Unknown fields, duplicate identifiers, booleans used as
integers, future source ticks, malformed IDs, and out-of-range values fail
closed.

For each source, linear logical-tick decay is applied to its absolute
magnitude. The remaining magnitude is divided by 1 + floor(distance in metres)
using integer Euclidean distance. The signed contribution is added to the
baseline. Contributions are summed in stable ID order. A combined value
outside the supported range is rejected rather than clamped.

## Determinism and limits

The returned frozen report sorts observations by point ID and includes a
SHA-256 consistency digest over canonical JSON. This is a reproducibility
witness, not authentication or authorization. Inputs are copied into bounded
primitive tuples before calculation; no caller mapping is retained.

The module uses only Python 3.11+ standard library. It performs no filesystem,
network, wall-clock, subprocess, native-engine, Aquarium, Noosphere, or device
access. The caller owns the meaning and provenance of all synthetic values.
