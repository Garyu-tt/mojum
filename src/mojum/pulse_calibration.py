"""Offline calibration audit for delayed synthetic pulse forecasts.

This module compares already-produced prior-tick probability forecasts with
their later binary labels. It does not train, infer, access journals, or make
physiological claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


SCHEMA = "mojum.neural.pulse-calibration/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_SAMPLES = 10_000
MAX_BINS = 100
MAX_TICK = (1 << 63) - 1


class CalibrationError(ValueError):
    """Invalid, ambiguous, or over-budget calibration input."""


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    index: int
    lower_bound: float
    upper_bound: float
    sample_count: int
    mean_forecast: float | None
    observed_frequency: float | None
    absolute_gap: float | None


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    schema: str
    provenance: str
    sample_count: int
    brier_score: float
    bins: tuple[CalibrationBin, ...]
    report_sha256: str


def _finite_probability(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except (OverflowError, TypeError, ValueError):
        return False


def _canonical_digest(payload: dict) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def audit_calibration(
    observations: list[dict] | tuple[dict, ...],
    *,
    source_classification: object = None,
    bin_count: int = 10,
) -> CalibrationReport:
    """Audit ordered {tick, forecast, observed} PUBLIC_SYNTHETIC pairs.

    forecast is the prior tick's probability for the current binary label.
    The function is read-only, bounded and deterministic. It rejects container
    subclasses and extra fields rather than invoking custom mapping behavior.
    Empty bins are included with None statistics.
    """
    if type(source_classification) is not str or source_classification != PROVENANCE:
        raise CalibrationError("explicit PUBLIC_SYNTHETIC classification required")
    if type(observations) not in (list, tuple):
        raise CalibrationError("observations must be a native list or tuple")
    if not 1 <= len(observations) <= MAX_SAMPLES:
        raise CalibrationError("observation count is outside the supported bounds")
    if type(bin_count) is not int or not 1 <= bin_count <= MAX_BINS:
        raise CalibrationError("bin_count must be an integer in [1, 100]")

    rows: list[tuple[int, float, bool]] = []
    previous_tick: int | None = None
    for index, row in enumerate(observations):
        if type(row) is not dict:
            raise CalibrationError(f"row {index} must be a native dict")
        if len(row) != 3:
            raise CalibrationError(f"row {index} has an unexpected field count")
        keys = tuple(row.keys())
        # All schema keys are at most eight ASCII characters. Reject long
        # caller-controlled names before hashing them in a temporary set.
        if any(type(key) is not str or len(key) > 8 for key in keys):
            raise CalibrationError(f"row {index} has an invalid field name")
        if set(keys) != {"tick", "forecast", "observed"}:
            raise CalibrationError(f"row {index} has an unexpected schema")

        tick = row["tick"]
        forecast = row["forecast"]
        observed = row["observed"]
        if type(tick) is not int or not 0 <= tick <= MAX_TICK:
            raise CalibrationError(f"row {index} tick must be an integer in [0, 2^63-1]")
        if previous_tick is not None and tick <= previous_tick:
            raise CalibrationError("ticks must be strictly increasing")
        if not _finite_probability(forecast):
            raise CalibrationError(f"row {index} forecast must be finite and in [0, 1]")
        if type(observed) is not bool:
            raise CalibrationError(f"row {index} observed label must be boolean")

        rows.append((tick, float(forecast), observed))
        previous_tick = tick

    counts = [0] * bin_count
    forecast_terms: list[list[float]] = [[] for _ in range(bin_count)]
    observed_counts = [0] * bin_count
    squared_errors: list[float] = []

    for _tick, forecast, observed in rows:
        # Use right-closed equal-width bins (first bin includes zero): values
        # exactly on an internal upper boundary remain in the lower bin.
        slot = max(0, math.ceil(forecast * bin_count) - 1)
        label = 1.0 if observed else 0.0
        counts[slot] += 1
        forecast_terms[slot].append(forecast)
        observed_counts[slot] += int(observed)
        error = forecast - label
        squared_errors.append(error * error)

    result_bins: list[CalibrationBin] = []
    digest_bins: list[dict] = []
    for slot in range(bin_count):
        count = counts[slot]
        if count:
            mean_forecast = math.fsum(forecast_terms[slot]) / count
            observed_frequency = observed_counts[slot] / count
            absolute_gap = abs(mean_forecast - observed_frequency)
        else:
            mean_forecast = observed_frequency = absolute_gap = None
        item = CalibrationBin(
            index=slot,
            lower_bound=slot / bin_count,
            upper_bound=(slot + 1) / bin_count,
            sample_count=count,
            mean_forecast=mean_forecast,
            observed_frequency=observed_frequency,
            absolute_gap=absolute_gap,
        )
        result_bins.append(item)
        digest_bins.append({
            "index": item.index,
            "lower_bound": item.lower_bound,
            "upper_bound": item.upper_bound,
            "sample_count": item.sample_count,
            "mean_forecast": item.mean_forecast,
            "observed_frequency": item.observed_frequency,
            "absolute_gap": item.absolute_gap,
        })

    brier_score = math.fsum(squared_errors) / len(rows)
    digest_payload = {
        "schema": SCHEMA,
        "provenance": PROVENANCE,
        "sample_count": len(rows),
        "brier_score": brier_score,
        "bins": digest_bins,
    }
    return CalibrationReport(
        schema=SCHEMA,
        provenance=PROVENANCE,
        sample_count=len(rows),
        brier_score=brier_score,
        bins=tuple(result_bins),
        report_sha256=_canonical_digest(digest_payload),
    )
