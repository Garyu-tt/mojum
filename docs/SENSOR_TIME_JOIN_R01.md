# R0.1: synthetic sensor triplet time join, issue #954 (parent #6)

The module mojum.sensor_time_join provides a **read-only**, CPU-first,
Python-stdlib-only audit of caller-owned synthetic observations. It is not a
neural model, sensor driver, world solver, privileged control path or release
certification. No journal/network/device/file I/O occurs.

## Input, source and time boundary

Call audit_sensor_time_frames(frames=[...], dt_ns=10_000_000, start_tick=0).
The nonempty ordered list/tuple holds at most 4096 consecutive frames, within
logical ticks 0..9998 of the existing v1 proprioception contract. dt_ns
must be an exact integer in [1,000, 1,000,000,000]. The exact common virtual
timestamp is tick * dt_ns, not wall-clock time. A frame has **only** these fields:

- tick: exact integer tick.
- interoception: exact Simulation sensory.interoception wire object:
  kind="interoception", observed_at_ns, and heartbeat with only beat_index
  (nonnegative integer), phase_milli (0..999), pulse (bool), synthetic=true
  (bool). Beat indices cannot regress. Beat cadence is **not** reconstructed
  from a hidden BPM setting.
- world: exact Simulation world.room wire object:
  kind="static-room-fixture", light in dark/dim/bright, observed_at_ns.
  Static-room light cannot change within one window.
- proprioception: exact Proprioception.public() v1 wire object, with
  schema="mojum.developmental.proprioception/1", tick, lowercase 64-hex
  world_revision, position_xy list of two finite 0..1000 numerics,
  heartbeat_pulse boolean and source="synthetic_proprioception_not_geometry".
  Pulse must match the interoceptive pulse; adjacent room revisions must differ
  (canonical point-room revision includes tick).

Every mapping must have precisely the supported keys and builtin value types.
Additional fields, mixed PRIVATE classifications, unknown versions, hostile
numeric types, omitted fields, nonfinite floats, stale clocks and gaps fail
closed with SensorJoinError and generic error messages. No partial witness is
returned. The caller constructs this already-public synthetic triplet from
existing producers; the API does not read private history or synthesize frames.

## Output and reproducibility

Returns a frozen SensorJoinWitness with schema
mojum.neural.synthetic-sensor-time-join/1, start tick, frame count, dt_ns,
first/last virtual nanoseconds and SHA-256. The digest uses ASCII JSON of the
schema/header and validated primitive per-tick metadata, each separated by
one newline byte. No frames, identities, files, raw payloads or input objects
are retained. Identical inputs create identical digests; changed metadata
changes the digest. This is **not** authentication or independent source
attestation. A malicious producer can forge internally consistent input
and recompute the digest.

## Explicit limitations

- This validates temporal alignment and typed admission, not physical
  validity of body pose, room revision hashes, beat cadence or neural learning.
- Does not invoke GENESIS, Aquarium ticks, a predictor, motor safety, MuJoCo,
  OpenXR, speech hardware, external adapters or device actuation.
- Does not support v2/epoch proprioception. Wrapping v1 tick numbers into
  epochs would silently reinterpret the persisted ABI; a distinct schema
  must be designed if required.
- PUBLIC_SYNTHETIC is established solely by whitelisted synthetic producer
  wire formats, not by trusted signatures. Do not pass PRIVATE sensor,
  memory or user payloads.
- Independent QA and exact-head GitHub CI required before merge. Parent #6
  remains open.

## Local verification

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_sensor_time_join.py' -v

The isolated focused run passed 18 tests on Python 3.13. Full repository and
multi-version GitHub CI remain independent review gates.
