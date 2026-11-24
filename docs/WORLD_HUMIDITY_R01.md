# Synthetic humidity field R0.1

**Status:** opt-in, CPU-only, synthetic metadata fixture. This is not a weather model or physical sensor.

## Contract

`HumidityProfile` binds an affine integer field to a 64-character lowercase SHA-256 room revision. Coordinates are integer millimetres; the room domain is half-open: `[0, width_mm) × [0, height_mm)`. Logical ticks are bounded to the exact-integer range `0..2^53-1` and cannot precede `epoch_tick`.

Humidity is represented in milli-percent, from 0 to 100,000. The sampled value is:

`base + floor(gx*x/1,000,000) + floor(gy*y/1,000,000) + drift*(tick-epoch_tick)`

The result is rejected if it falls outside 0..100 percent. Negative gradient terms use Python integer floor division. No wall clock is consulted.

Observations contain the pinned room revision, tick, position, synthetic provenance, an explicit `physical_sensor=false` marker, and a canonical SHA-256 consistency digest. `verify_observation` checks exact wire shape and digest; it does not authenticate the producer or prove physical truth.

## Limits and safety

- Standard library only; no runtime, Aquarium, SQLite, Noosphere, filesystem, network, device, or hardware integration.
- All public numeric inputs are exact native integers, not booleans or subclasses.
- Maximum room dimension: 1,000,000,000 mm.
- Maximum absolute spatial gradient: 100,000 milli-percent per km.
- Maximum absolute tick drift: 1,000 milli-percent per tick.
- Maximum logical tick: 2^53-1.
- Fixtures must be PUBLIC_SYNTHETIC and contain no private journal or user asset data.
