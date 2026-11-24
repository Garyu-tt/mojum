# Synthetic point-room pressure field R0.1

The opt-in pressure profile is a deterministic CPU-only fixture for bounded synthetic barometric-pressure observations. It is not a weather model, fluid solver, atmospheric simulation, calibrated measurement, or physical sensor.

## Units and bounds

- Pressure: integer pascals (Pa), supported output range 30,000..120,000 Pa.
- Position and room dimensions: integer millimetres (mm), using a half-open sample domain.
- Spatial gradients: integer Pa per kilometre.
- Temporal drift: integer Pa per logical tick; no wall clock is read.
- Room revision and tick are bound to the exact current canonical RoomState. The revision is recomputed against Room; stale, forged, or mismatched states fail closed.
- Samples inside room obstacles are rejected.

## Example

    room = Room(5.0, 4.0)
    state = initial_state(room, 1.0, 1.0)
    profile = PressureProfile(state.revision, 5000, 4000, 101000)
    sample = observe_pressure(profile, room=room, state=state,
                              tick=state.tick, x_mm=2000, y_mm=1500)

## Determinism and safety

The evaluator uses integer arithmetic, stable field names and canonical JSON SHA-256 consistency receipts. The verifier checks exact wire shape and digest. A digest is not authentication, provenance proof, or a signature. Inputs are caller-supplied PUBLIC_SYNTHETIC fixtures. The module performs no file/network access, hardware invocation, journal writes, or Aquarium/agent runtime wiring.

## Validation

Run offline with PYTHONPATH=src python -m unittest tests.test_world_pressure -v. The focused GitHub Actions workflow runs the suite on CPython 3.11, 3.12 and 3.13 against the exact pull-request head.
