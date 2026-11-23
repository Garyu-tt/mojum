# Body Observation Liveness Audit R0.1

This opt-in, pure-stdlib helper audits a caller-supplied sequence of
**PUBLIC_SYNTHETIC** body-observation frames. It checks whether the stream
meets declared sequence, sample-interval, and freshness bounds.

It does **not** acquire observations, authenticate a source, detect physical
life, evaluate posture or balance, prove a sensor is calibrated, or issue motor
commands. A passing report means only that the supplied synthetic records
satisfied the caller's declared timing policy.

## Frame contract

Each native dictionary has exactly these keys:

| Key | Contract |
| --- | --- |
| `schema` | `mojum.body.observation/1` |
| `visibility` | `PUBLIC_SYNTHETIC` |
| `consent` | `explicit` |
| `stream_id` | Stable nonempty identifier, max 96 characters |
| `source_id` | Stable nonempty source identifier, max 96 characters |
| `sequence` | Native nonnegative integer, strictly increasing |
| `sample_time_ns` | Native nonnegative monotonic timestamp, strictly increasing |

All records in one audit must have the same stream and source identity.
Sequence gaps are represented as inclusive ranges, never expanded into a list
of missing frame numbers. Maximum frame count is 4096 and canonical input is
limited to 1 MiB. Booleans are not accepted as integers.

## API

```python
from mojum.body_observation_liveness import audit_observation_liveness

report = audit_observation_liveness(
    frames,
    evaluated_at_ns=now_monotonic_ns,
    max_age_ns=250_000_000,
    max_sample_gap_ns=100_000_000,
)
```

The immutable report contains sequence gaps, the largest inter-sample interval,
freshness, the declared limits, a digest, and reason codes. `is_live` is true
only when the sequence is contiguous, every interval is within budget, and the
newest sample is neither stale nor in the future. A one-frame stream has no
measured inter-sample interval and is checked only for freshness.

The caller must provide `evaluated_at_ns` from the same monotonic clock domain
as `sample_time_ns`. The helper does not infer wall-clock offsets or repair
clock regressions. A SHA-256 digest is a reproducibility fingerprint, not an
authenticity claim.

## Tests

```bash
PYTHONPATH=src python -m unittest tests.test_body_observation_liveness -v
```

Python 3.11+; no external dependencies, I/O, network, persistent state,
hardware access, PRIVATE Noosphere input, or Aquarium/runtime wiring.
