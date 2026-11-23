"""Frozen, bounded multi-horizon audit for the synthetic delayed-pulse predictor.

This is an offline diagnostic. It never trains, writes a journal, or controls
an actuator. Future labels are used only after each prediction is produced.
"""
from dataclasses import dataclass
import hashlib
import json
import math

from mojum.neural import PulsePredictor

SCHEMA = "mojum.neural.pulse-horizon-audit/1"
INPUT_SCHEMA = "mojum.neural.pulse-horizon-input/1"
MAX_FRAMES = 4096
MAX_ORIGINS = 512
MAX_HORIZON = 32
MAX_TICK = (1 << 53) - 1
MAX_TIME_NS = (1 << 63) - 1
_FRAME_FIELDS = frozenset({"tick", "observed_at_ns", "phase_milli", "pulse"})
_SNAPSHOT_FIELDS = frozenset({
    "schema", "learning_rate", "weights", "pending_features",
    "updates", "loss_sum", "last_observed_at_ns",
})
_INPUT_FIELDS = frozenset({
    "schema", "provenance", "consent", "summation", "snapshot",
    "frames", "max_horizon", "max_origins",
})


class PulseHorizonError(ValueError):
    """Input is malformed, outside the declared budget, or not synthetic."""


@dataclass(frozen=True)
class HorizonMetric:
    horizon: int
    sample_count: int
    model_mae: float
    model_brier: float
    persistence_mae: float
    persistence_brier: float

    def to_dict(self):
        return {
            "horizon": self.horizon,
            "sample_count": self.sample_count,
            "model_mae": self.model_mae,
            "model_brier": self.model_brier,
            "persistence_mae": self.persistence_mae,
            "persistence_brier": self.persistence_brier,
        }


@dataclass(frozen=True)
class PulseHorizonReport:
    schema: str
    summation: str
    origin_count: int
    max_horizon: int
    snapshot_sha256: str
    inputs_sha256: str
    forecast_sha256: str
    metrics: tuple[HorizonMetric, ...]
    sha256: str

    def to_dict(self):
        """Return a detached JSON-safe report."""
        return {
            "schema": self.schema,
            "summation": self.summation,
            "origin_count": self.origin_count,
            "max_horizon": self.max_horizon,
            "snapshot_sha256": self.snapshot_sha256,
            "inputs_sha256": self.inputs_sha256,
            "forecast_sha256": self.forecast_sha256,
            "metrics": [item.to_dict() for item in self.metrics],
            "sha256": self.sha256,
            "provenance": "PUBLIC_SYNTHETIC",
            "training_performed": False,
            "future_labels_used_as_features": False,
            "physical_or_biological_claim": False,
        }


def _fail(message):
    raise PulseHorizonError(message)


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _canonical_hash(value):
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PulseHorizonError("value cannot be canonically encoded") from exc
    return hashlib.sha256(payload).hexdigest()


def _exact_dict(value, fields, what):
    if type(value) is not dict:
        _fail(f"{what} must be a native dict")
    # Check key types before set/frozenset equality to avoid hostile key methods.
    for key in value:
        if type(key) is not str:
            _fail(f"{what} contains a non-native string key")
    if len(value) != len(fields) or frozenset(value) != fields:
        _fail(f"{what} has missing or unexpected fields")


def _validate_snapshot(snapshot, summation):
    _exact_dict(snapshot, _SNAPSHOT_FIELDS, "snapshot")
    if type(snapshot["schema"]) is not str:
        _fail("snapshot schema must be a native string")
    if type(snapshot["schema"]) is not str or snapshot["schema"] != "mojum.neural.delayed-pulse/1":
        _fail("unsupported predictor snapshot schema")
    rate = snapshot["learning_rate"]
    if not _finite_number(rate) or not 0.0 < rate <= 0.5:
        _fail("invalid snapshot learning rate")
    weights = snapshot["weights"]
    if (type(weights) is not list or len(weights) != 3
            or any(not _finite_number(w) or not -2.0 <= w <= 2.0 for w in weights)):
        _fail("invalid snapshot weights")
    pending = snapshot["pending_features"]
    if pending is not None:
        if (type(pending) is not list or len(pending) != 3
                or any(not _finite_number(v) or not 0.0 <= v <= 1.0 for v in pending)
                or pending[0] != 1.0):
            _fail("invalid pending feature vector")
    updates = snapshot["updates"]
    loss_sum = snapshot["loss_sum"]
    last = snapshot["last_observed_at_ns"]
    if type(updates) is not int or not 0 <= updates <= MAX_TIME_NS:
        _fail("invalid snapshot update counter")
    if not _finite_number(loss_sum) or not 0.0 <= loss_sum <= updates:
        _fail("invalid snapshot loss counter")
    if last is None:
        if pending is not None or updates != 0:
            _fail("inconsistent uninitialized snapshot")
    elif type(last) is not int or not 0 <= last <= MAX_TIME_NS or pending is None or updates > last:
        _fail("invalid snapshot sensory clock")
    try:
        restored = PulsePredictor.from_snapshot(snapshot, summation=summation)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PulseHorizonError("canonical predictor snapshot validation failed") from exc
    # The core validator is the semantic authority. Keep only a detached weight
    # vector; no learner object or mutable pending feature list escapes this call.
    normalized = restored.snapshot()
    return tuple(float(value) for value in normalized["weights"])


def _sum_products(weights, features, summation):
    terms = [weight * feature for weight, feature in zip(weights, features)]
    if summation == "ordered":
        total = 0.0
        for term in terms:
            total += term
        return total
    total = correction = 0.0
    for term in terms:
        new_total = total + term
        if abs(total) >= abs(term):
            correction += (total - new_total) + term
        else:
            correction += (term - new_total) + total
        total = new_total
    return total + correction


def _clamp(value):
    return min(1.0, max(0.0, value))


def evaluate_pulse_horizons(document):
    """Evaluate a frozen autoregressive model on public synthetic pulse frames.

    Each origin starts with its observed pulse. For future ticks, the model
    receives the caller-known phase covariate and its own thresholded previous
    prediction. The corresponding true pulse is consulted only for scoring.
    A persistence baseline repeats the origin pulse at every horizon.
    """
    _exact_dict(document, _INPUT_FIELDS, "input")
    if type(document["schema"]) is not str or document["schema"] != INPUT_SCHEMA:
        _fail("unsupported input schema")
    if type(document["provenance"]) is not str or document["provenance"] != "PUBLIC_SYNTHETIC":
        _fail("PUBLIC_SYNTHETIC provenance is required")
    if type(document["consent"]) is not bool or document["consent"] is not True:
        _fail("explicit synthetic-data consent is required")
    summation = document["summation"]
    if type(summation) is not str or summation not in ("ordered", "compensated"):
        _fail("unsupported summation rule")
    max_horizon = document["max_horizon"]
    max_origins = document["max_origins"]
    if type(max_horizon) is not int or not 1 <= max_horizon <= MAX_HORIZON:
        _fail("max_horizon outside budget")
    if type(max_origins) is not int or not 1 <= max_origins <= MAX_ORIGINS:
        _fail("max_origins outside budget")
    frames = document["frames"]
    if type(frames) is not list or not max_horizon + 1 <= len(frames) <= MAX_FRAMES:
        _fail("frame count outside budget")
    weights = _validate_snapshot(document["snapshot"], summation)

    normalized_frames = []
    previous_tick = previous_time = None
    for index, frame in enumerate(frames):
        _exact_dict(frame, _FRAME_FIELDS, f"frame {index}")
        tick = frame["tick"]
        at = frame["observed_at_ns"]
        phase = frame["phase_milli"]
        pulse = frame["pulse"]
        if type(tick) is not int or not 0 <= tick <= MAX_TICK:
            _fail(f"invalid logical tick at frame {index}")
        if type(at) is not int or not 0 <= at <= MAX_TIME_NS:
            _fail(f"invalid timestamp at frame {index}")
        if type(phase) is not int or not 0 <= phase < 1000:
            _fail(f"invalid phase covariate at frame {index}")
        if type(pulse) is not bool:
            _fail(f"invalid synthetic pulse label at frame {index}")
        if previous_tick is not None and tick != previous_tick + 1:
            _fail("logical ticks must be consecutive")
        if previous_time is not None and at <= previous_time:
            _fail("timestamps must be strictly increasing")
        previous_tick, previous_time = tick, at
        normalized_frames.append({
            "tick": tick, "observed_at_ns": at,
            "phase_milli": phase, "pulse": pulse,
        })

    frames = normalized_frames
    origin_count = min(max_origins, len(frames) - max_horizon)
    if origin_count < 1:
        _fail("not enough frames for the selected horizon")

    forecast_rows = []
    model_abs = [0.0] * max_horizon
    model_sq = [0.0] * max_horizon
    baseline_abs = [0.0] * max_horizon
    baseline_sq = [0.0] * max_horizon
    # Each origin is rolled forward exactly once. No true future pulse is fed
    # back into the next step; it is consulted only for the score accumulator.
    for origin in range(origin_count):
        origin_pulse = frames[origin]["pulse"]
        previous_prediction_pulse = origin_pulse
        for step in range(1, max_horizon + 1):
            target = frames[origin + step]
            features = (
                1.0,
                target["phase_milli"] / 1000.0,
                float(previous_prediction_pulse),
            )
            prediction = _clamp(_sum_products(weights, features, summation))
            forecast_rows.append([frames[origin]["tick"], step, prediction])
            actual = float(target["pulse"])
            error = prediction - actual
            index = step - 1
            model_abs[index] += abs(error)
            model_sq[index] += error * error
            baseline_error = float(origin_pulse) - actual
            baseline_abs[index] += abs(baseline_error)
            baseline_sq[index] += baseline_error * baseline_error
            previous_prediction_pulse = prediction >= 0.5

    metric_rows = []
    for index in range(max_horizon):
        metric_rows.append(HorizonMetric(
            horizon=index + 1,
            sample_count=origin_count,
            model_mae=model_abs[index] / origin_count,
            model_brier=model_sq[index] / origin_count,
            persistence_mae=baseline_abs[index] / origin_count,
            persistence_brier=baseline_sq[index] / origin_count,
        ))

    snapshot_sha = _canonical_hash(document["snapshot"])
    inputs_sha = _canonical_hash({
        "schema": INPUT_SCHEMA,
        "summation": summation,
        "snapshot_sha256": snapshot_sha,
        "frames": frames,
        "max_horizon": max_horizon,
        "origin_count": origin_count,
    })
    forecast_sha = _canonical_hash({
        "schema": "mojum.neural.pulse-forecast/1",
        "snapshot_sha256": snapshot_sha,
        "summation": summation,
        "forecasts": forecast_rows,
    })
    report_body = {
        "schema": SCHEMA,
        "summation": summation,
        "origin_count": origin_count,
        "max_horizon": max_horizon,
        "snapshot_sha256": snapshot_sha,
        "inputs_sha256": inputs_sha,
        "forecast_sha256": forecast_sha,
        "metrics": [metric.to_dict() for metric in metric_rows],
    }
    return PulseHorizonReport(
        schema=SCHEMA,
        summation=summation,
        origin_count=origin_count,
        max_horizon=max_horizon,
        snapshot_sha256=snapshot_sha,
        inputs_sha256=inputs_sha,
        forecast_sha256=forecast_sha,
        metrics=tuple(metric_rows),
        sha256=_canonical_hash(report_body),
    )


def audit_pulse_horizons(document):
    """Public alias for callers that prefer the audit naming."""
    return evaluate_pulse_horizons(document)
