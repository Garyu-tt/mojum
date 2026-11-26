# World thermal field R0.1

## Purpose

An opt-in deterministic ambient-temperature observation fixture for the synthetic 2D point-room. It is a profile evaluator, not a heat-transfer solver, thermostat, physical sensor, weather model, or biological interoception system.

## API

mojum.world_thermal.observe_thermal_field(room, state, *, tick, expected_revision, profile, points_mm)

- Requires canonical mojum.world_room.Room and RoomState.
- Requires the exact current logical tick and matching room revision.
- Coordinates are integer millimetres, at most 256 points, within the room and outside closed obstacles.
- ThermalProfile uses integer milli-Celsius values and gradients per metre.
- Optional temporal variation is a deterministic triangle wave indexed only by logical tick. A zero period requires a zero amplitude.
- Returns a detached JSON-compatible result and SHA-256 digest over canonical result fields.

## Bounds and behavior

- CPU-only, Python standard library only, no wall clock, network, filesystem, subprocess, random source, or external package.
- Maximum room extent: 1,000,000 mm; maximum 256 query points.
- Base/absolute temperatures are bounded to ±200,000 milli-Celsius; gradients and cycle amplitude are bounded.
- Invalid types (including booleans masquerading as integers), stale revisions, obstacle points, and over-limit results fail closed.
- Output carries PUBLIC_SYNTHETIC provenance and physical_sensor: false.

## Verification

Run:

    PYTHONPATH=src python -m unittest tests.test_world_thermal -v

The dedicated workflow runs the focused tests on CPython 3.11, 3.12 and 3.13. This slice does not wire thermal observations into the Aquarium, sensory policy, Noosphere, or any hardware.
