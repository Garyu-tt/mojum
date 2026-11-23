# Body joint-acceleration audit R0.1

## Purpose

This opt-in helper audits a caller-supplied synthetic joint-position trajectory.
It computes a three-point finite-difference acceleration estimate for each
joint and compares the absolute value with a caller-declared per-joint limit.

It is a diagnostic fixture only. It does not control motors, read physical
sensors, import MuJoCo, calibrate anatomy, or establish actuator/biomechanical
safety. A report with no violations means only that the supplied synthetic
samples fit the supplied limits.

## Input contract

Call `audit_joint_acceleration(trajectory, acceleration_limits)`.

The trajectory is an exact native Python dictionary with exactly these fields:

| Field | Contract |
| --- | --- |
| `schema` | `mojum.body.joint-trajectory/1` |
| `classification` | `PUBLIC_SYNTHETIC` |
| `consent` | native boolean `True` |
| `stream_id` | nonempty bounded ASCII identifier |
| `joint_names` | 1..256 unique bounded ASCII names, ordered |
| `samples` | 3..4096 samples, in time order |

Each sample is an exact native dictionary with exactly `time_ns` and
`positions`. Time is a strictly increasing native integer in the signed
64-bit nonnegative range. Positions are a native list matching the joint
ordering and contain finite native integers/floats within the documented
bound. Boolean values are not numbers in this contract.

The total position matrix is capped at 65,536 scalar values in addition to
per-axis bounds, preventing a maximum-frame by maximum-joint Cartesian product.
The limits argument is an exact native dictionary whose keys exactly match
the joint names. Each limit is a positive, finite native int/float in
position-units per second squared. The input is intentionally in-memory only;
this helper does not parse untrusted JSON or read files.

## Computation

For three consecutive samples with left and right intervals `h0` and `h1`
(seconds), acceleration at the middle sample is:

`a = 2 * (((q2 - q1) / h1) - ((q1 - q0) / h0)) / (h0 + h1)`

This nonuniform three-point expression remains correct when sample spacing
changes. A violation is recorded only when `abs(a) > limit`. The report
contains the peak absolute estimate for each joint, an immutable tuple of
violations, sample/joint counts and a domain-separated SHA-256 digest of the
normalized input and diagnostic result.

## Failure and privacy boundaries

Malformed schemas, unknown fields, hostile mapping/container subclasses,
non-native key types, duplicate joints, invalid dimensions, non-monotonic
clocks, booleans-as-numbers, non-finite/out-of-bound values, and invalid
limits fail closed with `BodyAccelerationError`. The result never echoes
arbitrary caller payloads or raw sample arrays.

The digest is a deterministic consistency checksum, not authentication,
authorization, encryption, proof of provenance, or a physical safety
certificate. No filesystem, network, SQLite, PRIVATE Noosphere, runtime,
MuJoCo or motor-control integration is performed.

## Example

```python
from mojum.body_acceleration_audit import audit_joint_acceleration

trajectory = {
    "schema": "mojum.body.joint-trajectory/1",
    "classification": "PUBLIC_SYNTHETIC",
    "consent": True,
    "stream_id": "demo",
    "joint_names": ["elbow"],
    "samples": [
        {"time_ns": 0, "positions": [0.0]},
        {"time_ns": 1_000_000_000, "positions": [1.0]},
        {"time_ns": 2_000_000_000, "positions": [4.0]},
    ],
}
report = audit_joint_acceleration(trajectory, {"elbow": 1.5})
assert not report.passed
assert report.violations[0].acceleration == 2.0
```

## Verification

Run the focused fixture from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_body_acceleration_audit.py' -v
```

Python 3.11+ and the standard library are required. Full exact-head GitHub
Actions and independent peer review remain release gates; this document does
not claim that those checks have passed.
