# Frozen multi-horizon pulse forecast audit R0.1

This module measures how an existing synthetic one-step pulse predictor behaves when rolled forward for several logical ticks without observing the future pulse labels. It is an **offline diagnostic**, not a new learner or a claim about biology.

## Contract

- Input schema: `mojum.neural.pulse-horizon-input/1`; report schema: `mojum.neural.pulse-horizon-audit/1`.
- Requires explicit `PUBLIC_SYNTHETIC` provenance and `consent: true`.
- Restores and validates a detached `PulsePredictor` snapshot through the canonical predictor validator. The copied weights are frozen for the entire audit.
- For each origin, the current pulse is the initial autoregressive feedback. At every future tick, the model receives the supplied phase covariate and its own prior predicted pulse (threshold 0.5). A future true pulse is read only after that step's prediction, for scoring.
- Reports per-horizon sample count, model MAE/Brier score and a persistence baseline that repeats the origin pulse.
- A separate `forecast_sha256` binds the rollout predictions but excludes future labels. Changing only future labels therefore changes the input/score digests but not the forecast digest.
- Strict native Python types, consecutive ticks, increasing integer timestamps, finite bounded predictor state and exact versioned fields are required.

## Budgets

Python 3.11+, standard library plus the existing Mojum core. At most 4,096 frames, 512 forecast origins and 32 future steps. No third-party packages are installed or imported.

## Example

```python
from mojum.neural import PulsePredictor
from mojum.pulse_horizon_audit import audit_pulse_horizons

predictor = PulsePredictor(summation="compensated")
predictor.observe({
    "kind": "interoception", "observed_at_ns": 100,
    "heartbeat": {"phase_milli": 120, "pulse": False},
})
predictor.observe({
    "kind": "interoception", "observed_at_ns": 200,
    "heartbeat": {"phase_milli": 180, "pulse": True},
})
frames = [
    {"tick": i, "observed_at_ns": 1_000_000 + i * 1_000_000,
     "phase_milli": (i * 137) % 1000, "pulse": bool(i % 3)}
    for i in range(12)
]
document = {
    "schema": "mojum.neural.pulse-horizon-input/1",
    "provenance": "PUBLIC_SYNTHETIC", "consent": True,
    "summation": "compensated", "snapshot": predictor.snapshot(),
    "frames": frames, "max_horizon": 4, "max_origins": 8,
}
report = audit_pulse_horizons(document)
print(report.to_dict()["metrics"])
```

## Limitations

The future phase sequence is a caller-provided covariate, not a generated environment forecast. The pulse is a synthetic Boolean fixture label, not a real heartbeat or medical measurement. A multi-step rollout measures forecast behavior only; it does not establish learning, cognition, physiology, model quality on real data or improvement over a trained model. The digest is a deterministic consistency checksum, not authentication. No SQLite/Noosphere state is read or written, and this module is not wired into the Aquarium runtime or actuation path.

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_pulse_horizon_audit.py' -v
```
