"""Bounded offline evaluator for caller-measured synthetic IK benchmark samples.

This module evaluates supplied measurements; it does not implement FK/IK or run a solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

SCHEMA = "mojum.kinematics-benchmark/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_SAMPLES = 10_000
MAX_JOINTS = 512
MAX_ID_LENGTH = 128
MAX_ELAPSED_NS = 60_000_000_000
MAX_PEAK_BYTES = 1_099_511_627_776


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_ID_LENGTH:
        raise ValueError(f"invalid {field}")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError(f"invalid {field}")
    return value


def _vector(value: object, field: str) -> tuple[float | int, float | int, float | int]:
    if type(value) not in (tuple, list) or len(value) != 3:
        raise ValueError(f"{field} must contain exactly three coordinates")
    if not all(_finite(v) for v in value):
        raise ValueError(f"{field} must contain finite native numbers")
    return tuple(value)


@dataclass(frozen=True)
class SampleResult:
    sample_id: str
    residual_m: float
    elapsed_ns: int
    peak_bytes: int
    within_limits: bool
    max_limit_excursion_rad: float


@dataclass(frozen=True)
class BenchmarkReport:
    schema: str
    provenance: str
    rig_revision: str
    sample_count: int
    mean_residual_m: float
    max_residual_m: float
    mean_elapsed_ns: float
    max_elapsed_ns: int
    max_peak_bytes: int
    within_limits: bool
    budget_exceeded: bool
    samples: tuple[SampleResult, ...]
    digest: str


def evaluate_ik_samples(
    samples: object,
    joint_limits: object,
    *,
    rig_revision: str,
    provenance: str = PROVENANCE,
    max_elapsed_ns: int | None = None,
    max_peak_bytes: int | None = None,
) -> BenchmarkReport:
    """Validate and summarize bounded, caller-supplied FK/IK observations.

    Coordinates are metres, joint positions/limits are radians, elapsed time is
    nanoseconds, and peak memory is bytes. No solver is invoked and no contact
    or physical-world inference is made.
    """
    rig_revision = _identifier(rig_revision, "rig_revision")
    if type(provenance) is not str or provenance != PROVENANCE:
        raise ValueError("only explicit PUBLIC_SYNTHETIC provenance is accepted")
    if type(samples) not in (list, tuple) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise ValueError("invalid sample collection size")
    if type(joint_limits) is not dict or not 1 <= len(joint_limits) <= MAX_JOINTS:
        raise ValueError("joint_limits must be a bounded non-empty dict")
    if (max_elapsed_ns is not None and
            (type(max_elapsed_ns) is not int or not 0 <= max_elapsed_ns <= MAX_ELAPSED_NS)):
        raise ValueError("invalid max_elapsed_ns")
    if (max_peak_bytes is not None and
            (type(max_peak_bytes) is not int or not 0 <= max_peak_bytes <= MAX_PEAK_BYTES)):
        raise ValueError("invalid max_peak_bytes")

    limits: dict[str, tuple[float | int, float | int]] = {}
    for raw_name, raw_bounds in joint_limits.items():
        name = _identifier(raw_name, "joint id")
        if type(raw_bounds) not in (tuple, list) or len(raw_bounds) != 2:
            raise ValueError("each joint limit must be a lower/upper pair")
        lower, upper = raw_bounds
        if not _finite(lower) or not _finite(upper) or lower >= upper:
            raise ValueError("joint limits must be finite and strictly increasing")
        limits[name] = (lower, upper)

    seen: set[str] = set()
    normalized = []
    for raw in samples:
        if type(raw) is not dict or set(raw) != {
            "sample_id", "target_m", "achieved_m", "joints", "elapsed_ns", "peak_bytes"
        }:
            raise ValueError("sample has missing or unknown fields")
        sample_id = _identifier(raw["sample_id"], "sample_id")
        if sample_id in seen:
            raise ValueError("duplicate sample_id")
        seen.add(sample_id)
        target = _vector(raw["target_m"], "target_m")
        achieved = _vector(raw["achieved_m"], "achieved_m")
        joints = raw["joints"]
        if type(joints) is not dict or set(joints) != set(limits):
            raise ValueError("sample joint set does not match rig limits")
        for value in joints.values():
            if not _finite(value):
                raise ValueError("joint positions must be finite native numbers")
        elapsed = raw["elapsed_ns"]
        peak = raw["peak_bytes"]
        if type(elapsed) is not int or not 0 <= elapsed <= MAX_ELAPSED_NS:
            raise ValueError("invalid elapsed_ns")
        if type(peak) is not int or not 0 <= peak <= MAX_PEAK_BYTES:
            raise ValueError("invalid peak_bytes")
        residual = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(target, achieved)))
        excursions = []
        for name, value in joints.items():
            lower, upper = limits[name]
            excursions.append(max(float(lower) - float(value), 0.0, float(value) - float(upper)))
        normalized.append((sample_id, residual, elapsed, peak, max(excursions, default=0.0)))

    normalized.sort(key=lambda item: item[0])
    results = tuple(
        SampleResult(sample_id, residual, elapsed, peak, excursion == 0.0, excursion)
        for sample_id, residual, elapsed, peak, excursion in normalized
    )
    count = len(results)
    mean_residual = math.fsum(item.residual_m for item in results) / count
    mean_elapsed = math.fsum(item.elapsed_ns for item in results) / count
    all_within = all(item.within_limits for item in results)
    budget_exceeded = any(
        (max_elapsed_ns is not None and item.elapsed_ns > max_elapsed_ns)
        or (max_peak_bytes is not None and item.peak_bytes > max_peak_bytes)
        for item in results
    )
    canonical = {
        "schema": SCHEMA,
        "provenance": provenance,
        "rig_revision": rig_revision,
        "joint_limits": {name: list(limits[name]) for name in sorted(limits)},
        "samples": [
            {
                "sample_id": item.sample_id,
                "residual_m": item.residual_m,
                "elapsed_ns": item.elapsed_ns,
                "peak_bytes": item.peak_bytes,
                "within_limits": item.within_limits,
                "max_limit_excursion_rad": item.max_limit_excursion_rad,
            }
            for item in results
        ],
        "max_elapsed_ns": max_elapsed_ns,
        "max_peak_bytes": max_peak_bytes,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return BenchmarkReport(
        SCHEMA, provenance, rig_revision, count, mean_residual,
        max(item.residual_m for item in results), mean_elapsed,
        max(item.elapsed_ns for item in results), max(item.peak_bytes for item in results),
        all_within, budget_exceeded, results, digest,
    )
