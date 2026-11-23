# R0.1 passive virtual-physics trace audit (issue #5)

`mojum.physics_trace_audit.audit_physics_trace` is a CPU-only, dependency-free,
read-only temporal audit of *synthetic* virtual-physics observation records.
It composes the existing `mojum.physics_observation` packet admission contract.
It does not connect to MuJoCo, assert true physics, actuate joints, control
hardware, authorize action, or interact with Noosphere or the network.

For exactly `PUBLIC_SYNTHETIC` records and explicit `consent=True`, submit an
exact Python `list` or `tuple` containing **1..32** complete observation dicts.
Supply `start_tick` (signed 63-bit nonnegative), `start_time_ns` (same bound)
and `step_ns` (integer 1..1,000,000,000). Every frame must have tick
`start_tick + index` and timestamp `start_time_ns + index * step_ns` without
63-bit overflow; all frames must use the same `fixture` or `mujoco` backend
and identical joint IDs, although joint ordering and measured positions may
vary. Individual packets retain the existing bounds (up to 256 joints and
4096 virtual contacts); the full trace allows at most **4,096 joint records**
and **8,192 virtual-contact records**. Any failure raises `ValueError` without
returning a partial receipt.

Successful output includes only schema, classification, frame count, backend,
aggregate counts, endpoint ticks/timestamps, step size, and `trace_sha256`.
The digest binds the complete canonical validated numeric observations and
contact data. It is a reproducible **checksum, not a signature**, and does not
prove engine identity, sample timing, physical realism, or informed consent
outside the caller-supplied flag. The output never includes raw joint IDs,
positions, velocities, force measurements, geometry IDs or host identifiers.
Input containers are not mutated. Do not use the hash as a privacy guarantee
for sensitive data; PRIVATE traces are refused by the explicit classification
contract. Nothing is persisted or transmitted.

Run focused tests from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_physics_trace_audit.py' -v
```

This is an isolated BUILD proposal for independent QA and exact-head CI before
merging to `main`. It deliberately does **not** edit the existing physics
observation schema, runtime, world/body barrier, actuator safety or history.
