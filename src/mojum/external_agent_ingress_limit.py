"""Bounded, process-local admission policy for authenticated receipt ingress.

This module is deliberately not an HTTP server or an authentication mechanism.
Callers must authenticate a source before invoking the limiter.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
import threading

_SOURCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}", re.ASCII)


@dataclass(frozen=True, slots=True)
class Admission:
    allowed: bool
    reason: str
    remaining: int
    retry_after_ns: int


@dataclass(slots=True)
class _Window:
    started_ns: int
    used: int


class IngressRateLimiter:
    """Thread-safe fixed-window limiter with bounded source cardinality.

    Time is supplied by the caller from a monotonic clock. A global rollback
    is rejected without mutating state. Expired source windows are pruned
    deterministically; active entries are never evicted to make room.
    """

    def __init__(
        self,
        *,
        limit: int = 30,
        window_ns: int = 60_000_000_000,
        max_sources: int = 1024,
    ) -> None:
        if type(limit) is not int or not 1 <= limit <= 1_000_000:
            raise ValueError("limit must be a native integer in [1, 1000000]")
        if type(window_ns) is not int or not 1 <= window_ns <= 86_400_000_000_000:
            raise ValueError("window_ns must be a native integer in [1, 86400000000000]")
        if type(max_sources) is not int or not 1 <= max_sources <= 100_000:
            raise ValueError("max_sources must be a native integer in [1, 100000]")
        self.limit = limit
        self.window_ns = window_ns
        self.max_sources = max_sources
        self._windows: OrderedDict[str, _Window] = OrderedDict()
        self._last_now_ns: int | None = None
        self._lock = threading.Lock()

    def admit(self, source_id: str, now_ns: int) -> Admission:
        """Record one already-authenticated attempt and return its decision."""
        if type(source_id) is not str or _SOURCE_RE.fullmatch(source_id) is None:
            return Admission(False, "invalid_source", 0, 0)
        if type(now_ns) is not int or not 0 <= now_ns <= 2**63 - 1:
            return Admission(False, "invalid_clock", 0, 0)

        with self._lock:
            if self._last_now_ns is not None and now_ns < self._last_now_ns:
                return Admission(False, "clock_rollback", 0, 0)
            self._last_now_ns = now_ns

            expired = [
                key for key, value in self._windows.items()
                if now_ns - value.started_ns >= self.window_ns
            ]
            for key in expired:
                del self._windows[key]

            window = self._windows.get(source_id)
            if window is None:
                if len(self._windows) >= self.max_sources:
                    return Admission(False, "capacity_exhausted", 0, self.window_ns)
                self._windows[source_id] = _Window(now_ns, 1)
                return Admission(True, "allowed", self.limit - 1, 0)

            if window.used >= self.limit:
                retry = max(0, window.started_ns + self.window_ns - now_ns)
                return Admission(False, "rate_limited", 0, retry)

            window.used += 1
            self._windows.move_to_end(source_id)
            return Admission(True, "allowed", self.limit - window.used, 0)

    @property
    def tracked_sources(self) -> int:
        with self._lock:
            return len(self._windows)
