# Mojum R0.4 — opt-in reactive synthetic closed-loop fixture

**Status: proposed development branch; not a released autonomous Dante brain.**

This increment closes a *minimal* control loop in the existing CPU-only point-room
simulation: **current synthetic heartbeat + current room position + last committed
collision feedback -> deterministic bounded fixture policy -> point-room
displacement -> collision/position feedback -> private SQLite journal -> replay**.

The neural `PulsePredictor` remains a separate learning *observer*; it does not
choose actions. The graph's `brain.placeholder` stays `UNIMPLEMENTED`. The
reactive controller is explicitly **hard-coded and non-learning**. There is no
MuJoCo contact, robotic motor command, calibrated joint model, or real-world
sensor connection. No installed network service or external hardware is touched.

## Run (from repository checkout, stdlib only)

```bash
PYTHONPATH=src python -m mojum.aquarium --db ./dante_reactive_r04.sqlite3 --ticks 160 --control-mode reactive_fixture
PYTHONPATH=src python -m mojum.aquarium --db ./dante_reactive_r04.sqlite3 --ticks 60 --control-mode reactive_fixture
PYTHONPATH=src python -m unittest discover -s tests -v
```

Use a **new database** for reactive mode. Existing manual databases are
not silently migrated. Explicit `--dx` and `--dy` are rejected in reactive
mode rather than overriding the controller. Ordinary R0.3/manual mode keeps
the same bootstrap schema, unchanged frame shape and default displacement.

## Determinism and boundaries

- The bootstrap manifest pins `control_mode`, policy schema and goal only for
  opted-in reactive journals. On reopening, a mismatched control mode or goal
  fails closed before resuming.
- The policy reads only committed position / prior world blockage and current
  synthetic interoception. It tries X toward a target; after an X collision,
  it tries Y for one tick. Pulse controls a **2 cm or 4 cm** simulated step.
  This is a simple heuristic, not path planning, learned exploration, or
  guaranteed obstacle avoidance.
- The point-room's swept collision test remains authoritative. Controller
  output is never sent to `MotorGate`, MuJoCo, devices or external processes.
- The movement intention, observation metadata and resulting world response
  are stored inside the private event journal. On replay, the controller's
  movement is **recomputed**; the journal cannot dictate a different movement
  merely by recording `movement_request`.
- Controller feedback, world position, neural observer state and time advance
  in memory only **after** the SQLite append succeeds. Reopen derives the same
  controller feedback from committed frames. CAS conflicts do not advance RAM.
- Replay is still limited to 10,000 events. Journal SHA-256 is an integrity
  check, not authentication, encryption, distributed consensus or privacy
  enforcement against a host administrator.

## Next step after independent QA

A learned policy should receive explicitly scoped sensory observations and
emit only **proposals**, validated by a separate spinal/motor safety gate
before any simulated body actuation. Add structured perception of obstacle
geometry, controller-version migrations, cross-component checkpoint barriers,
and then a bounded MuJoCo adapter behind independent capabilities. Never
present this fixture controller as a neural motor system or a sentient agent.
