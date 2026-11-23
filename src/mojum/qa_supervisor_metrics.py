"""Bounded, deterministic and privacy-safe QA supervisor metrics.

This module reduces caller-supplied snapshots only. It performs no I/O and
has no scheduling, polling, persistence, or authorization authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping
from typing import Any

MAX_TASKS = 10_000
MAX_DURATION_SAMPLES = 10_000
MAX_ID_LENGTH = 128
MAX_AGE_SECONDS = 10 * 365 * 24 * 60 * 60
MAX_DURATION_MS = 24 * 60 * 60 * 1000

STATES = (
    "DISCOVERED", "CLAIMED", "REVIEWING", "TESTING", "FIXING",
    "RETESTING", "READY", "MERGED", "WAITING_CI",
    "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED",
)
ACTIVE_STATES = frozenset({"CLAIMED", "REVIEWING", "TESTING", "FIXING", "RETESTING"})
BLOCKED_STATES = frozenset({"WAITING_CI", "BLOCKED_PERMISSION", "BLOCKED_CONFLICT", "ESCALATED"})
BLOCK_REASONS = frozenset({
    "CI_PENDING", "PERMISSION", "CONFLICT", "DEPENDENCY", "PROVIDER",
    "RATE_LIMIT", "REVIEW_REQUIRED", "OTHER",
})
AGE_BUCKETS = ("lt_5m", "5m_to_1h", "1h_to_1d", "1d_to_7d", "ge_7d")


class MetricsInputError(ValueError):
    """Raised when a snapshot is malformed or exceeds resource bounds."""


def _integer(value: Any, field: str, *, minimum: int = 0, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MetricsInputError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise MetricsInputError(f"{field} is outside the permitted range")
    return value


def _finite_number(value: Any, field: str, *, maximum: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricsInputError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > maximum:
        raise MetricsInputError(f"{field} is outside the permitted range")
    return result


def _age_bucket(seconds: int) -> str:
    if seconds < 300:
        return "lt_5m"
    if seconds < 3600:
        return "5m_to_1h"
    if seconds < 86400:
        return "1h_to_1d"
    if seconds < 604800:
        return "1d_to_7d"
    return "ge_7d"


def _nearest_rank(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered) / 100) - 1)
    return round(ordered[index], 3)


def summarize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return aggregate metrics and a SHA-256 receipt for a bounded snapshot.

    Accepted top-level fields: observed_at (non-negative epoch seconds),
    tasks (up to 10,000 task mappings), and optional duration_samples_ms.
    Task IDs are validated for uniqueness but never copied into the result.
    """
    if not isinstance(snapshot, Mapping):
        raise MetricsInputError("snapshot must be a mapping")
    if set(snapshot) - {"observed_at", "tasks", "duration_samples_ms"}:
        raise MetricsInputError("snapshot contains unknown fields")
    if "observed_at" not in snapshot or "tasks" not in snapshot:
        raise MetricsInputError("observed_at and tasks are required")

    observed_at = _integer(snapshot["observed_at"], "observed_at", maximum=4_102_444_800)
    tasks = snapshot["tasks"]
    if not isinstance(tasks, (list, tuple)) or len(tasks) > MAX_TASKS:
        raise MetricsInputError("tasks must be a bounded list")

    ids: set[str] = set()
    states: Counter[str] = Counter()
    ages: Counter[str] = Counter()
    blocked: Counter[str] = Counter()
    leases = Counter({"active": 0, "expiring": 0, "expired": 0, "missing": 0})
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise MetricsInputError(f"task {index} must be a mapping")
        allowed = {"id", "state", "age_seconds", "blocked_reason", "lease_remaining_seconds"}
        if set(task) - allowed or not {"id", "state"} <= set(task):
            raise MetricsInputError(f"task {index} has an invalid schema")
        task_id = task["id"]
        if not isinstance(task_id, str) or not task_id or len(task_id) > MAX_ID_LENGTH:
            raise MetricsInputError(f"task {index} has an invalid id")
        if task_id in ids:
            raise MetricsInputError("duplicate task id")
        ids.add(task_id)

        state = task["state"]
        if not isinstance(state, str) or state not in STATES:
            raise MetricsInputError(f"task {index} has an unknown state")
        states[state] += 1

        if "age_seconds" in task:
            age = _integer(task["age_seconds"], f"task {index} age_seconds", maximum=MAX_AGE_SECONDS)
            ages[_age_bucket(age)] += 1

        reason = task.get("blocked_reason")
        if state in BLOCKED_STATES and reason is not None:
            if not isinstance(reason, str) or reason not in BLOCK_REASONS:
                raise MetricsInputError(f"task {index} has an unknown blocked reason")
            blocked[reason] += 1
        elif reason is not None:
            raise MetricsInputError(f"task {index} has a reason outside a blocked state")

        lease_present = "lease_remaining_seconds" in task
        if state in ACTIVE_STATES:
            if not lease_present:
                leases["missing"] += 1
            else:
                remaining = _finite_number(
                    task["lease_remaining_seconds"],
                    f"task {index} lease_remaining_seconds",
                    maximum=MAX_AGE_SECONDS,
                )
                if remaining <= 0:
                    leases["expired"] += 1
                elif remaining <= 30:
                    leases["expiring"] += 1
                else:
                    leases["active"] += 1
        elif lease_present:
            raise MetricsInputError(f"task {index} has a lease outside an active state")

    samples = snapshot.get("duration_samples_ms", ())
    if not isinstance(samples, (list, tuple)) or len(samples) > MAX_DURATION_SAMPLES:
        raise MetricsInputError("duration_samples_ms must be a bounded list")
    durations = [
        _finite_number(value, f"duration_samples_ms[{i}]", maximum=MAX_DURATION_MS)
        for i, value in enumerate(samples)
    ]

    payload: dict[str, Any] = {
        "schema": "mojum.qa-supervisor-metrics.v1",
        "observed_at": observed_at,
        "task_count": len(tasks),
        "state_counts": {key: states[key] for key in STATES if states[key]},
        "age_bucket_counts": {key: ages[key] for key in AGE_BUCKETS if ages[key]},
        "blocked_reason_counts": {key: blocked[key] for key in sorted(blocked)},
        "lease_health": {key: leases[key] for key in ("active", "expiring", "expired", "missing")},
        "duration_sample_count": len(durations),
        "duration_ms": {
            "p50": _nearest_rank(durations, 50),
            "p95": _nearest_rank(durations, 95),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload
