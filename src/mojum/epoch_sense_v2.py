"""Strict absolute-time, explicitly epoch-scoped synthetic proprioception /2.

This version has no 9999-tick wraparound or hidden replay compatibility with
the old Proprioception /1. Old policy and history are intentionally untouched.
"""
from dataclasses import dataclass
import math
import re

SCHEMA = "mojum.developmental.proprioception/2"
EPOCH_TICKS = 9999
MAX_ABSOLUTE_TICKS = 99_999  # independent bounded streaming journal remains
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class EpochSenseError(ValueError):
    """Invalid absolute/epoch-proprioceptive causal observation."""


@dataclass(frozen=True)
class EpochProprioception:
    absolute_tick: int
    epoch_index: int
    epoch_tick: int
    revision: str
    x: float
    y: float
    pulse: bool

    def __post_init__(self):
        if (type(self.absolute_tick) is not int
                or not 0 <= self.absolute_tick < MAX_ABSOLUTE_TICKS
                or type(self.epoch_index) is not int
                or self.epoch_index != self.absolute_tick // EPOCH_TICKS
                or type(self.epoch_tick) is not int
                or self.epoch_tick != self.absolute_tick % EPOCH_TICKS
                or type(self.revision) is not str
                or _REVISION.fullmatch(self.revision) is None
                or type(self.x) not in (int, float)
                or type(self.y) not in (int, float)
                or type(self.pulse) is not bool):
            raise EpochSenseError("invalid absolute/epoch proprioception")
        try:
            x, y = float(self.x), float(self.y)
        except (OverflowError, TypeError, ValueError) as exc:
            raise EpochSenseError("invalid absolute/epoch proprioception") from exc
        if (not math.isfinite(x) or not math.isfinite(y)
                or not 0 <= x <= 1000 or not 0 <= y <= 1000):
            raise EpochSenseError("invalid absolute/epoch proprioception")

    @property
    def tick(self):
        """Absolute monotonic logical tick, never a wrapped epoch offset."""
        return self.absolute_tick

    def public(self):
        return {
            "schema": SCHEMA,
            "absolute_tick": self.absolute_tick,
            "epoch_index": self.epoch_index,
            "epoch_tick": self.epoch_tick,
            "world_revision": self.revision,
            "position_xy": [float(self.x), float(self.y)],
            "heartbeat_pulse": self.pulse,
            "source": "synthetic_epoch_proprioception_not_geometry",
        }
