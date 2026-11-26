# Mojum standing-window candidate auditor R01

**Issue:** #923 (narrow additive slice under #5). **Classification:** OFFLINE /
PUBLIC_SYNTHETIC; never merge without independent QA and exact-head CI.

## Contract and boundaries

`mojum.standing_window_audit.audit_standing_window` accepts 2..512
consecutive `mojum.virtual-physics-observation/1` packets (already admitted
by the canonical passive observation validator) and produces a deterministic
JSON-compatible `mojum.standing-window-candidate/1` receipt. The input list,
frames and nested dictionaries are not modified. Everything runs in CPU-only
Python 3.11+ stdlib with no files, sockets, GPU, live engine, runtime imports,
policy changes, persistent state or PRIVATE Noosphere access.

A **stationary-support candidate** means only that (1) observations are
consecutive at an explicitly specified fixed nanosecond timestep; (2) declared
backend and joint inventory are stable; (3) every sample has at least the
configured total virtual floor-contact normal force; (4) all reported joint
angular speeds stay at/below the configured threshold; and (5) elapsed
observation time reaches the requested threshold, default **5,000,000,000 ns**.

The receipt **always** sets `standing_milestone_achieved=false`,
`perturbation_recovery_achieved=false` and `source_authenticated=false`.
This is not an actual standing test, balance measurement, gait analysis, or
contact source authentication. It does not observe center of mass (CoM),
center of pressure (CoP), geometric support polygon, body tilt, horizontal
movement, motor controller performance or recovery after perturbation.
Static support and low joint speed can coexist with falling or braced bodies.
The #5 5s standing and perturbation recovery acceptance gates remain
**unachieved** until a separate reviewed engine-backed test establishes them.

The #907 virtual CoM/CoP witness is separate. This module intentionally does
not import #907 or modify #689 MuJoCo, #833 batch safety, #900 physics trace,
#912 motor slew, canonical Aquarium or the protected `main` branch.

## Usage

```python
from mojum.standing_window_audit import audit_standing_window

# frames: caller-supplied, PUBLIC_SYNTHETIC versioned observation dictionaries
receipt = audit_standing_window(
    frames,
    expected_dt_ns=20_000_000,       # 20ms
    required_duration_ns=5_000_000_000,
    min_floor_force_n=1.0,
    max_joint_speed_rad_s=0.1,
)
# receipt["stationary_support_candidate"] is a heuristic, not a safety proof
```

The duration counts **last timestamp minus first timestamp**. At 20ms,
exactly 251 observations are required for a 5s candidate; 250 observations
produce `insufficient_duration`. More than 512 frames are refused. Synthetic
floor geometry must have an endpoint named exactly `floor`; other contacts
cannot be inferred as support.

The versioned `evidence_sha256` is SHA-256 over each canonical validated
packet with a newline delimiter. The `receipt_sha256` binds threshold
settings, computed metrics, evidence hash, and result flags. Neither digest
authenticates the source, authorizes physical motion, establishes engine
provenance or protects against rollback. Never treat a caller-provided
`backend="mujoco"` tag as proof the MuJoCo engine executed.

## Failure rules and resource budget

Structural errors, invalid/hostile dictionary keys, unsupported backend,
non-synthetic/physical claims, bad numeric values, duplicate IDs, exceeding
96 joints / 16 contacts per frame, inconsistent joint sets, nonconsecutive
ticks or exact nanosecond clock gaps raise `StandingWindowError` before any
receipt is produced. A valid but short, unsupported or moving trace returns
a `stationary_support_candidate=false` receipt with stable reason order.

Maximum frames 512, one virtual joint set, at most 96 joints and 16
contacts in each frame, dt from 1ns to 1s, required duration 1ns to 60s.
All ticks and nanosecond timestamps remain signed-63-bit nonnegative.
There is no unbounded history, external subprocess, network, or persistence.

## Reproduce

```sh
PYTHONPATH=src python -m unittest tests.test_standing_window_audit -v
# or
PYTHONPATH=src python -m unittest discover -s tests -p 'test_standing_window_audit.py' -v
```

Independent reviewers must verify exact HEAD under Python 3.11, 3.12, 3.13,
all applicable repository GitHub Actions, and exercise fabricated equality
selectors, malformed data, overflow, short windows and contact losses before
any merge. No hardware or PRIVATE traces should be used in public CI.
