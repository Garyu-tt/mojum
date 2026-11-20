"""Pure CPU-only, fail-closed motor-intent validation fixture.

No hardware actuation, MuJoCo integration, calibrated anatomy or security boundary
against malicious Python code running in this very process.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType


@dataclass(frozen=True)
class MotorIntent:
    joint: str
    target: float
    revision: int


def _is_finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


class MotorGate:
    """Fixed-limit validator, read-only public state and process-latched e-stop."""

    __slots__ = ("_limits", "_revision", "_estop_latched")

    def __init__(self, limits: dict[str, tuple[float, float]], revision: int = 0):
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid revision")
        if not isinstance(limits, Mapping) or not limits or len(limits) > 512:
            raise ValueError("limits must be a nonempty bounded mapping")
        checked = {}
        for name, pair in limits.items():
            if (not isinstance(name, str) or not name.strip() or
                    not isinstance(pair, (tuple, list)) or len(pair) != 2):
                raise ValueError("invalid joint definition")
            lo, hi = pair
            if not _is_finite_number(lo) or not _is_finite_number(hi) or lo >= hi:
                raise ValueError("invalid limits")
            checked[name] = (float(lo), float(hi))
        self._limits = MappingProxyType(checked)
        self._revision = revision
        self._estop_latched = False

    @property
    def limits(self):
        """Read-only copy-normalized joint limits; no mutable public bypass."""
        return self._limits

    @property
    def revision(self):
        return self._revision

    @property
    def estop_latched(self):
        return self._estop_latched

    def trip_estop(self):
        self._estop_latched = True

    def validate(self, intent: MotorIntent) -> float:
        if self._estop_latched:
            raise PermissionError("e-stop latched")
        if not isinstance(intent, MotorIntent):
            raise TypeError("MotorIntent required")
        if type(intent.revision) is not int or intent.revision != self._revision:
            raise ValueError("stale revision")
        if not isinstance(intent.joint, str) or intent.joint not in self._limits:
            raise ValueError("unknown joint")
        if not _is_finite_number(intent.target):
            raise ValueError("invalid target")
        lo, hi = self._limits[intent.joint]
        if not lo <= intent.target <= hi:
            raise ValueError("target outside configured bounds")
        return float(intent.target)

    def restore_revision(self, revision: int):
        """Restore is monotonic and never clears an emergency stop."""
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid revision")
        if revision < self._revision:
            raise ValueError("revision rollback forbidden")
        self._revision = revision

