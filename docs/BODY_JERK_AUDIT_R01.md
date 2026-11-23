# Body joint-jerk audit R0.1

## Purpose

This opt-in helper audits a caller-supplied synthetic joint-position
trajectory. For every four-sample window it estimates the third derivative
of position (jerk) and compares its absolute magnitude with a
caller-declared per-joint limit.

This is a diagnostic fixture only. It does not control motors, read physical
sensors, import MuJoCo, calibrate anatomy, or establish actuator or
biomechanical safety. A report with no violations means only that the supplied
synthetic samples fit the supplied limits.

## API and input contract

Call:

```python
from mojum.body_jerk_audit import audit_joint_jerk

report = audit_joint_jerk(trajectory, jerk_limits)
```

The trajectory is an exact native Python dictionary with exactly these fields:

| Field | Contract |
| --- | --- |
| `schema` | `mojum.body.joint-trajectory/1` |
| `classification` | `PUBLIC_SYNTHETIC` |
| `consent` | native boolean `True` |
| `stream_id` | nonempty bounded ASCII identifier |
| `joint_names` | 1..256 unique bounded ASCII names, ordered |
| `samples` | 4..4096 samples, in time order |

Each sample is an exact native dictionary with exactly `time_ns` and
`positions`. Time is a strictly increasing native integer in the signed
64-bit nonnegative range. Positions are native lists matching the joint
ordering and contain finite native integers/floats within the documented
bound. Boolean values are not numbers in this contract.

The total position matrix is capped at 65,536 scalar values in addition to
per-axis bounds. The `jerk_limits` argument is an exact native dictionary
whose keys exactly match the joint names. Every limit is positive, finite and
bounded, expressed in position-units per second cubed. The input is
in-memory only; this helper does not parse JSON or read files.

## Numerical method

For each window of four samples, timestamps are first differenced as integers
in nanoseconds and only then converted to seconds. This preserves 1 ns
resolution even when timestamps are near the signed 64-bit upper bound.

With times (t_0,t_1,t_2,t_3) and positions (q_0,q_1,q_2,q_3), form the
nonuniform divided differences:

```text
d1[0] = (q1-q0)/(t1-t0)
d1[1] = (q2-q1)/(t2-t1)
d1[2] = (q3-q2)/(t3-t2)

d2[0] = (d1[1]-d1[0])/(t2-t0)
d2[1] = (d1[2]-d1[1])/(t3-t1)

jerk = 6 * (d2[1]-d2[0])/(t3-t0)
```

Times in the equations are seconds. Six times the third divided difference
is the exact third derivative for a cubic position trajectory, including
nonuniform sample spacing. For arbitrary sampled motion it is a windowed
finite-difference estimate, not a continuous-time physical measurement.
A violation is recorded only when `abs(jerk) > limit`. The report includes
per-joint peak absolute jerk, immutable violation records, counts and a
domain-separated SHA-256 digest of the normalized inputs and results.

## Failure and privacy boundaries

Malformed schemas, unknown fields, hostile mapping/container subclasses,
non-native key types, duplicate joints, invalid dimensions, non-monotonic
clocks, booleans-as-numbers, non-finite/out-of-bound values and invalid limits
fail closed with `BodyJerkError`. The report never echoes arbitrary caller
payloads or raw sample arrays.

The digest is a deterministic consistency checksum, not authentication,
authorization, encryption, proof of provenance or a physical safety
certificate. No filesystem, network, SQLite, PRIVATE Noosphere, runtime,
MuJoCo or motor-control integration is performed.

## Example

```python
trajectory = {
    "schema": "mojum.body.joint-trajectory/1",
    "classification": "PUBLIC_SYNTHETIC",
    "consent": True,
    "stream_id": "demo",
    "joint_names": ["elbow"],
    "samples": [
        {"time_ns": 0, "positions": [0.0]},
        {"time_ns": 1_000_000_000, "positions": [1.0]},
        {"time_ns": 2_000_000_000, "positions": [8.0]},
        {"time_ns": 3_000_000_000, "positions": [27.0]},
    ],
}
report = audit_joint_jerk(trajectory, {"elbow": 5.5})
assert not report.passed
assert abs(report.violations[0].jerk - 6.0) < 1e-12
```

## Verification

Run the focused suite from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_body_jerk_audit.py' -v
```

Python 3.11+ and the standard library are required. Full exact-head GitHub
Actions and independent peer review remain release gates; this document does
not claim those checks have passed.
