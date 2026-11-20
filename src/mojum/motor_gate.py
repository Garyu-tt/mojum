"""Pure CPU-only motor intention safety gate; never actuates hardware.

This fixture validates commands against an explicit revision and latched e-stop.
It does not implement physical limits, calibrated anatomy, or MuJoCo dynamics.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MotorIntent:
    joint: str
    target: float
    revision: int


class MotorGate:
    def __init__(self, limits: dict[str, tuple[float, float]], revision: int = 0):
        if type(revision) is not int or revision < 0:
            raise ValueError('invalid revision')
        if not limits:
            raise ValueError('limits required')
        self.limits = {}
        for name, pair in limits.items():
            if not isinstance(name, str) or not name or not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError('invalid joint definition')
            lo, hi = pair
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in pair) or lo >= hi:
                raise ValueError('invalid limits')
            self.limits[name] = (float(lo), float(hi))
        self.revision = revision
        self.estop_latched = False

    def trip_estop(self):
        self.estop_latched = True

    def validate(self, intent: MotorIntent) -> float:
        if self.estop_latched:
            raise PermissionError('e-stop latched')
        if not isinstance(intent, MotorIntent):
            raise TypeError('MotorIntent required')
        if type(intent.revision) is not int or intent.revision != self.revision:
            raise ValueError('stale revision')
        if intent.joint not in self.limits:
            raise ValueError('unknown joint')
        value = intent.target
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError('invalid target')
        lo, hi = self.limits[intent.joint]
        if not lo <= value <= hi:
            raise ValueError('target outside configured bounds')
        return float(value)

    def restore_revision(self, revision: int):
        """Snapshot restore cannot clear an e-stop or silently reset revisions."""
        if type(revision) is not int or revision < 0:
            raise ValueError('invalid revision')
        if revision < self.revision:
            raise ValueError('revision rollback forbidden')
        self.revision = revision
