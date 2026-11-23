# Virtual physics observation envelope, R0.1

Issue #689; parent #5. This is a **passive input schema**, not an integrated MuJoCo engine, trained body, IK solver or authenticated engine readout.

Import `validate_virtual_physics_observation` from `mojum.physics_observation` to validate an opt-in `mojum.virtual-physics-observation/1` packet. Required keys: `schema`, `tick`, `time_ns`, `backend` (`fixture` or `mujoco`), `synthetic: true`, `physical_contact: false`, `physical_actuation: false`, `joints` (bounded id/position/velocity), and `virtual_contacts` (bounded geometries/normal force).

Invariants:
- Preserve **virtual vs physical** contact semantics: even nonzero MuJoCo forces never claim external hardware contact or actuation.
- No global import of MuJoCo, no side effects or network, no persistence migration. The Python stdlib-only point-room runtime remains available unchanged.
- Reject nonfinite or implausibly large geometry measurements, booleans pretending to be numbers, duplicate joints, unknown schemas/keys and bad identifiers.
- Return a detached canonical JSON object. Validation verifies *shape only*; authentication of the producing engine, model identity, coordinate frame, deterministic numeric replay, asset licenses and trusted MJCF compilation are distinct acceptance gates.
- Later opt-in MuJoCo implementation must pin the exact engine/model versions, dt, joint order, reference frame, units and DOF mapping, and must use separate motor-safety approval before any physical actuator could be wired.

Run: `PYTHONPATH=src python -m unittest tests.test_physics_observation -v`.
