"""Deterministic, side-effect-free polling backoff and circuit-breaker policy.

This module computes *when* a caller may poll. It performs no I/O, sleeps,
random jitter, credential handling, or GitHub operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from typing import Literal

Outcome = Literal["success", "transient", "rate_limited", "forbidden", "permanent"]
CircuitStatus = Literal["closed", "open", "half_open"]


class PollPolicyError(ValueError):
    """Raised when an untrusted polling snapshot is malformed."""


def _int(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PollPolicyError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def parse_retry_after(value: str | int | float | None, *, now_epoch: float) -> float | None:
    """Parse Retry-After delta-seconds or HTTP-date into a bounded delay.

    Returns None for absent/invalid values. The caller supplies now_epoch so
    the result is deterministic and testable. A valid server hint is capped
    at 24 hours; negative, non-finite and boolean values are rejected.
    """
    if value is None:
        return None
    if type(value) in (int, float):
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return None
        if not math.isfinite(number) or number < 0:
            return None
        return min(number, 86400.0)
    if type(value) is not str or len(value) > 128:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
        if math.isfinite(seconds) and seconds >= 0:
            return min(seconds, 86400.0)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if not math.isfinite(float(now_epoch)):
            return None
        return min(max(0.0, dt.timestamp() - float(now_epoch)), 86400.0)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None


def backoff_delay(
    consecutive_failures: int,
    *,
    base_seconds: float = 2.0,
    max_seconds: float = 300.0,
    retry_after: str | int | float | None = None,
    now_epoch: float = 0.0,
) -> float:
    """Return capped exponential delay, respecting a valid Retry-After hint."""
    _int(consecutive_failures, "consecutive_failures", 0, 31)
    if (type(now_epoch) not in (int, float) or not math.isfinite(now_epoch) or now_epoch < 0):
        raise PollPolicyError("now_epoch must be a finite non-negative number")
    if (type(base_seconds) not in (int, float) or type(max_seconds) not in (int, float)
            or not math.isfinite(base_seconds) or not math.isfinite(max_seconds)
            or base_seconds <= 0 or max_seconds < base_seconds or max_seconds > 86400):
        raise PollPolicyError("invalid backoff bounds")
    exponential = min(float(max_seconds), float(base_seconds) * (2 ** consecutive_failures))
    hint = parse_retry_after(retry_after, now_epoch=now_epoch)
    return min(float(max_seconds), max(exponential, hint or 0.0))


@dataclass(frozen=True)
class Circuit:
    status: CircuitStatus = "closed"
    failures: int = 0
    opened_at: float | None = None
    probe_in_flight: bool = False


def circuit_transition(
    circuit: Circuit,
    outcome: Outcome,
    *,
    now_epoch: float,
    failure_threshold: int = 5,
    reset_after: float = 60.0,
) -> Circuit:
    """Apply one completed poll result; no transition grants write authority."""
    _validate_circuit(circuit)
    if type(outcome) is not str or outcome not in ("success", "transient", "rate_limited", "forbidden", "permanent"):
        raise PollPolicyError("unknown outcome")
    if type(now_epoch) not in (int, float) or not math.isfinite(now_epoch) or now_epoch < 0:
        raise PollPolicyError("now_epoch must be a finite non-negative number")
    _int(failure_threshold, "failure_threshold", 1, 100)
    if type(reset_after) not in (int, float) or not math.isfinite(reset_after) or reset_after <= 0:
        raise PollPolicyError("reset_after must be positive and finite")

    if outcome == "success":
        return Circuit()
    if outcome in ("forbidden", "permanent"):
        # Permission and permanent errors are not transient network failures.
        return Circuit(status="open", failures=max(circuit.failures, failure_threshold),
                       opened_at=float(now_epoch), probe_in_flight=False)
    failures = min(100, circuit.failures + 1)
    if circuit.status == "half_open" or failures >= failure_threshold:
        return Circuit(status="open", failures=failures, opened_at=float(now_epoch),
                       probe_in_flight=False)
    return Circuit(status="closed", failures=failures)


def _validate_circuit(circuit: object) -> None:
    """Reject malformed public circuit snapshots before making policy decisions."""
    if type(circuit) is not Circuit:
        raise PollPolicyError("circuit must be an exact Circuit")
    if type(circuit.status) is not str or circuit.status not in ("closed", "open", "half_open"):
        raise PollPolicyError("circuit has an unknown status")
    _int(circuit.failures, "circuit.failures", 0, 100)
    if type(circuit.probe_in_flight) is not bool:
        raise PollPolicyError("circuit.probe_in_flight must be bool")
    if circuit.opened_at is not None:
        if (type(circuit.opened_at) not in (int, float)
                or not math.isfinite(circuit.opened_at) or circuit.opened_at < 0):
            raise PollPolicyError("circuit.opened_at must be a finite non-negative number")
    if circuit.status == "closed":
        if circuit.opened_at is not None or circuit.probe_in_flight:
            raise PollPolicyError("closed circuit cannot have an open timestamp or active probe")
    elif circuit.opened_at is None:
        raise PollPolicyError("open and half-open circuits require opened_at")
    elif circuit.status == "open" and circuit.probe_in_flight:
        raise PollPolicyError("open circuit cannot have a probe in flight")


def poll_permitted(circuit: Circuit, *, now_epoch: float, reset_after: float = 60.0) -> bool:
    """Whether one read-only poll may start; half-open permits exactly one probe."""
    _validate_circuit(circuit)
    if type(now_epoch) not in (int, float) or not math.isfinite(now_epoch) or now_epoch < 0:
        raise PollPolicyError("now_epoch must be finite and non-negative")
    if type(reset_after) not in (int, float) or not math.isfinite(reset_after) or reset_after <= 0:
        raise PollPolicyError("reset_after must be positive and finite")
    if circuit.status == "closed":
        return True
    if circuit.status == "half_open":
        return not circuit.probe_in_flight
    return circuit.opened_at is not None and now_epoch >= circuit.opened_at + reset_after


def begin_probe(circuit: Circuit, *, now_epoch: float, reset_after: float = 60.0) -> Circuit:
    """Move an expired open circuit to half-open and reserve its sole probe."""
    if not poll_permitted(circuit, now_epoch=now_epoch, reset_after=reset_after):
        raise PollPolicyError("poll is not currently permitted")
    if circuit.status == "closed":
        return circuit
    return Circuit(status="half_open", failures=circuit.failures,
                   opened_at=circuit.opened_at, probe_in_flight=True)
