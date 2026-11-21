"""Strict absolute-time, explicitly epoch-scoped synthetic proprioception.

This is a new wire contract.  The historical ``Proprioception/1`` type keeps
its 9,999-tick domain so old journals are never reinterpreted or silently
wrapped at an epoch boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re


SCHEMA = "mojum.developmental.proprioception/2"
EPOCH_TICKS = 9_999
# 100,000 is the source journal ceiling.  Leave room for the nine durable
# markers needed before this endpoint (bootstrap + frames + markers).
MAX_ABSOLUTE_TICKS = 99_990
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class EpochSenseError(ValueError):
    """Invalid absolute or epoch-scoped proprioceptive observation."""


def _finite_position(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1000
    except (OverflowError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class EpochProprioception:
    """Own position and heartbeat pulse with an absolute monotonic tick."""

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
                or not _finite_position(self.x)
                or not _finite_position(self.y)
                or type(self.pulse) is not bool):
            raise EpochSenseError("invalid absolute/epoch proprioception")

    @property
    def tick(self):
        """Absolute logical tick; it never wraps to an epoch-local offset."""
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
