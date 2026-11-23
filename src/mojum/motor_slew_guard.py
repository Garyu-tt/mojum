"""Bounded adjacent-tick slew limiter for a CPU-only *virtual* motor fixture.

This is not a physical actuator, control-loop watchdog, or safety-certified
controller. It makes no clocks, I/O, journal writes, or MuJoCo calls.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
from types import MappingProxyType

SCHEMA = "mojum.motor.slew-guard/1"
MAX_JOINTS = 128
MAX_TICK = 2**63 - 1
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


class MotorSlewError(ValueError):
    """A virtual motor frame or configuration failed its contract."""


def _integer(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TICK:
        raise MotorSlewError(f"{field} must be an integer in [0, {MAX_TICK}]")
    return value


def _finite(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise MotorSlewError(f"{field} must be a finite int or float")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MotorSlewError(f"{field} must be finite") from exc
    if not math.isfinite(normalized):
        raise MotorSlewError(f"{field} must be finite")
    # Binary64 silently rounds some exact integers (notably 2**53 + 1).
    # Never turn an out-of-bounds requested target into an in-bounds one.
    if type(value) is int and normalized != value:
        raise MotorSlewError(f"{field} loses integer precision")
    return normalized


def _joint_name(name: object) -> str:
    if type(name) is not str or _NAME.fullmatch(name) is None:
        raise MotorSlewError("joint names must be 1..64 safe ASCII characters")
    return name


def _mapping(value: object, field: str) -> dict:
    # Exact dicts exclude custom Mapping accessors and surprising comparisons.
    if type(value) is not dict:
        raise MotorSlewError(f"{field} must be a plain dict")
    return value


@dataclass(frozen=True)
class VirtualFrameReceipt:
    """Immutable accepted-state observation, not a cryptographic proof."""

    schema: str
    tick: int
    revision: int
    targets: tuple[tuple[str, float], ...]


class MotorSlewGuard:
    """Explicit opt-in guard for sequential virtual joint-position targets.

    ``limits`` is a plain dictionary of joint names to exactly three numeric
    values ``(min_target, max_target, max_change_per_tick)``. ``initial`` must
    supply every joint. Tick and revision start at zero unless explicitly
    provided, and each accepted frame advances both by precisely one.

    The guard is deliberately memory-only. Its process-local e-stop is latched
    permanently; constructing a *new* guard is an external explicit decision,
    not a reset/restore API. It does not create, store, or forward motor commands.
    """

    __slots__ = ("_limits", "_targets", "_tick", "_revision", "_estop", "_lock")

    def __init__(
        self,
        limits: dict[str, tuple[float, float, float]],
        initial: dict[str, float],
        *,
        tick: int = 0,
        revision: int = 0,
    ) -> None:
        _integer(tick, "tick")
        _integer(revision, "revision")
        values = _mapping(limits, "limits")
        if not 1 <= len(values) <= MAX_JOINTS:
            raise MotorSlewError(f"limits must contain 1..{MAX_JOINTS} joints")
        checked: dict[str, tuple[float, float, float]] = {}
        for name, definition in values.items():
            _joint_name(name)
            if type(definition) is not tuple or len(definition) != 3:
                raise MotorSlewError(f"limits[{name}] must be a 3-tuple")
            lo, hi, slew = (
                _finite(definition[0], f"limits[{name}].min"),
                _finite(definition[1], f"limits[{name}].max"),
                _finite(definition[2], f"limits[{name}].slew"),
            )
            if not lo < hi or not math.isfinite(hi - lo) or not slew > 0:
                raise MotorSlewError(f"invalid virtual joint bounds or slew: {name}")
            checked[name] = (lo, hi, slew)

        starting = _mapping(initial, "initial")
        if starting.keys() != checked.keys():
            raise MotorSlewError("initial must contain exactly the configured joints")
        targets = self._normalize_targets(starting, checked)
        self._limits = MappingProxyType(checked)
        self._targets = MappingProxyType(targets)
        self._tick = tick
        self._revision = revision
        self._estop = False
        self._lock = threading.RLock()

    @staticmethod
    def _normalize_targets(
        targets: dict, limits: dict | MappingProxyType
    ) -> dict[str, float]:
        normalized = {}
        # Verify exactly identical key set before ever indexing a configured joint.
        if targets.keys() != limits.keys():
            raise MotorSlewError("target frame must contain every joint exactly once")
        for name in sorted(limits):
            target = _finite(targets[name], f"target[{name}]")
            lo, hi, _ = limits[name]
            if not lo <= target <= hi:
                raise MotorSlewError(f"target outside configured bounds: {name}")
            normalized[name] = target
        return normalized

    @property
    def tick(self) -> int:
        with self._lock:
            return self._tick

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def estop_latched(self) -> bool:
        with self._lock:
            return self._estop

    def trip_estop(self) -> None:
        """Irreversibly prevent any further admission by this instance."""
        with self._lock:
            self._estop = True

    def receipt(self) -> VirtualFrameReceipt:
        """Return immutable and detached data describing the current frame."""
        with self._lock:
            return VirtualFrameReceipt(
                SCHEMA, self._tick, self._revision,
                tuple(sorted(self._targets.items())),
            )

    def advance(
        self,
        targets: dict[str, float],
        *,
        tick: int,
        expected_revision: int,
    ) -> VirtualFrameReceipt:
        """Admit one whole virtual frame, or leave all state unchanged.

        The caller supplies the **next** tick and the **current** revision.
        Long gaps are rejected rather than granting a larger velocity budget.
        """
        # Serialize admission and e-stop so the optimistic revision check and
        # the state commit are one atomic operation across Python threads.
        with self._lock:
            if self._estop:
                raise MotorSlewError("e-stop latched")
            requested_tick = _integer(tick, "tick")
            expected = _integer(expected_revision, "expected_revision")
            if (self._tick == MAX_TICK or self._revision == MAX_TICK or
                    requested_tick != self._tick + 1 or
                    expected != self._revision):
                raise MotorSlewError("stale or non-adjacent virtual frame")
            frame = self._normalize_targets(_mapping(targets, "targets"), self._limits)
            for name, next_target in frame.items():
                _lo, _hi, max_change = self._limits[name]
                if abs(next_target - self._targets[name]) > max_change:
                    raise MotorSlewError(f"slew limit exceeded: {name}")
            # Everything validated. Commit one new immutable mapping in one step.
            self._targets = MappingProxyType(frame)
            self._tick = requested_tick
            self._revision += 1
            return self.receipt()
