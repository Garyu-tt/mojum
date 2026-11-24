# World first-order echo geometry R0.1

## Purpose

`mojum.world.echo-observation/1` is an opt-in, CPU-only, deterministic geometric fixture for first-order echoes from the four outer walls of the synthetic 2D point-room.

The implementation uses the image-source construction: mirror a source across one room boundary, intersect the listener-to-image line with that boundary, and measure the two physical path legs. At most four candidate echoes are considered per source event.

## Contract

- Inputs are canonical `Room` and `RoomState` values plus caller-supplied sound events explicitly shaped as `mojum.world.synthetic-sound/1`.
- Room revision and logical tick are checked before geometry is evaluated.
- Up to 16 unique events; event age, source level, coordinates, path range and exact native types are bounded.
- Each candidate is a first-order specular reflection from one outer wall: `left`, `right`, `bottom`, or `top`.
- A rectangular obstacle intersecting either leg blocks that candidate. Obstacles are opaque; they are not treated as reflectors.
- Path lengths and bounce points are reported in integer millimetres. Output ordering and SHA-256 consistency receipt are deterministic.

## Explicit limits

This is **not** waveform synthesis, a microphone/speaker, room impulse response, material absorption, diffraction, multi-bounce reverberation, calibrated SPL, frequency-dependent propagation, or a biological hearing model. It does not estimate actual sound pressure or guarantee perceptual audibility. The simple inverse-distance level is only a synthetic fixture metric.

No changes to canonical room state, Aquarium, journals, PRIVATE Noosphere, devices, runtime wiring, or mandatory dependencies.
