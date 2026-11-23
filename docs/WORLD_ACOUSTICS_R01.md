# World acoustic event sensor R0.1

## Purpose
An opt-in deterministic, read-only sound-event observation for the synthetic
point-room. The contract can later be replaced by measured or physically
modelled audio without changing canonical room state.

## Contract
- Events use `mojum.world.synthetic-sound/1`: bounded ID, logical source tick,
  room-local XY position and caller-supplied integer source level in milli-units.
- The listener is a canonical `RoomState`; its room revision is checked first.
- At most 32 unique events are accepted. IDs must be valid UTF-8, source
  positions must lie in free room space, and future/expired events are refused.
- Distances are integer millimetres. A deterministic inverse-distance rule
  attenuates levels. Any room obstacle intersecting the source-listener segment
  fully occludes that event.
- Results are sorted by distance then event ID. A SHA-256 digest supports
  integrity/determinism checks but is **not authentication**.

## Limits
This is not waveform processing, a microphone, a speaker, reverberation,
frequency analysis, diffraction, material absorption, room impulse response,
calibrated SPL, or a biological hearing model. Attenuation and full obstacle
occlusion are simple geometric fixture rules only. No real audio is captured
or emitted.

No runtime wiring, persistence, network access, mandatory third-party
dependencies, physical-device access or actuator authority is introduced.

## Verification
`PYTHONPATH=src python -m unittest discover -s tests -p 'test_world_acoustics.py' -v`
