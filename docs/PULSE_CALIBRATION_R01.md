# Pulse forecast calibration audit R0.1

## Purpose

`mojum.pulse_calibration.audit_calibration` compares already-produced prior-tick
probability forecasts with later binary outcomes. It is a small offline
measurement utility for the synthetic delayed-pulse predictor. It does not train
or evaluate a learned policy and does not claim biological or medical meaning.

## Contract

- Schema: `mojum.neural.pulse-calibration/1`.
- The caller must explicitly pass `source_classification="PUBLIC_SYNTHETIC"`;
  missing, private, or malformed classifications fail closed.
- Input is a native list/tuple of 1..10,000 native dictionaries containing
  exactly `tick`, `forecast`, and `observed`.
- Ticks are exact integers in [0, 2^63-1] and strictly increase.
- Forecasts are exact native integers/floats in [0, 1]; booleans, NaN and
  infinities are rejected.
- Outcomes must be exact booleans. Container subclasses, unknown keys and
  malformed shapes fail closed.
- Bin count is an exact integer in [1, 100]. Empty bins are included and have
  null statistics.

The detached frozen report contains the Brier score, sample count, per-bin mean
forecast, observed frequency and absolute calibration gap, plus a SHA-256
consistency digest over the canonical report fields. The digest detects
accidental changes to this report representation; it is not a signature,
authentication token or proof of source provenance.

## Example

```python
from mojum.pulse_calibration import audit_calibration

report = audit_calibration([
    {"tick": 10, "forecast": 0.15, "observed": False},
    {"tick": 11, "forecast": 0.22, "observed": False},
    {"tick": 12, "forecast": 0.81, "observed": True},
], source_classification="PUBLIC_SYNTHETIC", bin_count=5)

print(report.brier_score)
for item in report.bins:
    if item.sample_count:
        print(item.index, item.mean_forecast, item.observed_frequency)
```

## Reproduce

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_pulse_calibration.py' -v
```

## Boundaries

- Uses only the Python standard library and caller-supplied in-memory samples.
- No model, weights, learning state, journal, filesystem, network or runtime
  integration is accessed.
- A low Brier score is a descriptive score on the supplied sample only. It does
  not establish generalization, independence of held-out data, causality,
  physiological validity or organism cognition.
- Calibration bins are descriptive and depend on the selected bin count.
  Small samples can yield unstable observed frequencies.
- Consumers must ensure that each forecast was made before its paired outcome
  was observed. The auditor validates strictly increasing logical ticks but
  cannot prove the caller's causal ordering or dataset independence.
