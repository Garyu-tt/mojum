"""Pure, bounded HTTP retry decisions for opt-in Spartan model providers.

No IO, sleeps, secrets, wall clock reads or provider-specific state. A caller
must independently enforce idempotency, quotas, token/USD budgets and leases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

SCHEMA = "mojum.spartan.http-retry-decision/1"
RETRYABLE_STATUSES = frozenset((408, 429, 500, 502, 503, 504))
_DELTA_SECONDS = re.compile(r"[0-9]{1,10}\Z", re.ASCII)
_MAX_HEADER_LENGTH = 64
# The HTTP wire grammar is English, independently of host LC_TIME settings.
_IMF_DATE = re.compile(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), "
    r"([0-9]{2}) "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT\Z",
    re.ASCII,
)
_MONTHS = {name: n for n, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
)}
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MAX_DELAY_MS = 86_400_000  # 24 hours, not an unlimited server delay


def _native_int(name: str, value: object, lower: int, upper: int) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be a native integer in [{lower}, {upper}]")


def _utc_datetime(name: str, value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError(f"{name} must be a native UTC datetime")
    return value


def _milliseconds_ceil(duration: timedelta) -> int:
    """Exact, integer-ceiling milliseconds, including microsecond precision."""
    microseconds = (
        (duration.days * 86_400 + duration.seconds) * 1_000_000
        + duration.microseconds
    )
    return max(0, (microseconds + 999) // 1_000)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Finite bounds. max_attempts includes the original request."""

    max_attempts: int = 5
    initial_delay_ms: int = 250
    max_backoff_ms: int = 30_000
    max_retry_after_ms: int = 120_000

    def __post_init__(self) -> None:
        _native_int("max_attempts", self.max_attempts, 1, 32)
        _native_int("initial_delay_ms", self.initial_delay_ms, 1, _MAX_DELAY_MS)
        _native_int("max_backoff_ms", self.max_backoff_ms, 1, _MAX_DELAY_MS)
        _native_int("max_retry_after_ms", self.max_retry_after_ms, 0, _MAX_DELAY_MS)
        if self.initial_delay_ms > self.max_backoff_ms:
            raise ValueError("initial_delay_ms cannot exceed max_backoff_ms")


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Detached decision; delay_ms is zero whenever should_retry is false."""

    should_retry: bool
    delay_ms: int
    reason: str
    schema: str = SCHEMA


def _deny(reason: str) -> RetryDecision:
    return RetryDecision(False, 0, reason)


def _server_delay_ms(header: str, now: datetime) -> int | None:
    """Parse only RFC 9110 delta-seconds or canonical IMF-fixdate GMT.

    Return None for invalid input; never silently fall back when a server
    supplied a malformed header. Negative dates mean no extra server delay.
    """
    if not header or len(header) > _MAX_HEADER_LENGTH or not header.isascii():
        return None
    if _DELTA_SECONDS.fullmatch(header):
        return int(header) * 1_000
    # Strict, canonical IMF-fixdate, rejecting obs-date, weekday mismatches,
    # timezone offsets, whitespace and HTTP response-splitting characters.
    if len(header) != 29:
        return None
    match = _IMF_DATE.fullmatch(header)
    if match is None:
        return None
    day_name, day, month_name, year, hour, minute, second = match.groups()
    try:
        server_utc = datetime(
            int(year), _MONTHS[month_name], int(day),
            int(hour), int(minute), int(second), tzinfo=timezone.utc,
        )
        if _WEEKDAYS[server_utc.weekday()] != day_name:
            return None
        return _milliseconds_ceil(server_utc - now)
    except (ValueError, OverflowError):
        return None


def plan_retry(
    *,
    status_code: int,
    failed_attempt: int,
    now_utc: datetime,
    replay_safe: bool,
    retry_after: str | None = None,
    deadline_utc: datetime | None = None,
    remaining_wait_ms: int | None = None,
    policy: RetryPolicy = RetryPolicy(),
) -> RetryDecision:
    """Plan, but do not execute, a provider request retry.

    failed_attempt is 1 for the first failed request. The caller must
    explicitly assert replay_safe after enforcing idempotency and cost
    accounting. Deadline and remaining wait budget are independently checked;
    no early retry is allowed to bypass a server Retry-After minimum.
    """
    if type(policy) is not RetryPolicy:
        raise ValueError("policy must be a native RetryPolicy")
    _native_int("status_code", status_code, 100, 599)
    _native_int("failed_attempt", failed_attempt, 1, policy.max_attempts)
    now = _utc_datetime("now_utc", now_utc)
    if type(replay_safe) is not bool:
        raise ValueError("replay_safe must be a native bool")
    if retry_after is not None and type(retry_after) is not str:
        raise ValueError("retry_after must be a native string or None")
    if deadline_utc is not None:
        _utc_datetime("deadline_utc", deadline_utc)
    if remaining_wait_ms is not None:
        _native_int("remaining_wait_ms", remaining_wait_ms, 0, _MAX_DELAY_MS)

    if status_code not in RETRYABLE_STATUSES:
        return _deny("non_retryable_status")
    if not replay_safe:
        return _deny("unsafe_replay")
    if failed_attempt >= policy.max_attempts:
        return _deny("attempts_exhausted")

    # Bound exponent by max_attempts <= 32. The local backoff cap is separate
    # from the server Retry-After cap; both must fit the caller's deadline.
    backoff = min(
        policy.max_backoff_ms,
        policy.initial_delay_ms * (1 << (failed_attempt - 1)),
    )
    extra = 0
    if retry_after is not None:
        parsed = _server_delay_ms(retry_after, now)
        if parsed is None:
            return _deny("invalid_retry_after")
        if parsed > policy.max_retry_after_ms:
            return _deny("retry_after_exceeds_limit")
        extra = parsed
    delay = max(backoff, extra)

    if remaining_wait_ms is not None and delay > remaining_wait_ms:
        return _deny("wait_budget_exceeded")
    if deadline_utc is not None and _milliseconds_ceil(deadline_utc - now) < delay:
        return _deny("deadline_exceeded")
    try:
        now + timedelta(milliseconds=delay)
    except OverflowError:
        return _deny("clock_overflow")
    return RetryDecision(True, delay, "retry_allowed")
