"""Strict absolute-time, explicitly epoch-scoped synthetic proprioception.

The legacy :mod:`mojum.local_cell_sense` contract intentionally stops at
9,999 logical ticks.  This separate wire schema carries both a monotonic
absolute tick and its epoch-local coordinates; old journals never enter this
module and are not reinterpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


SCHEMA = "mojum.developmental.proprioception/2"
EPOCH_TICKS = 9_999
MAX_ABSOLUTE_TICKS = 99_999
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class EpochSenseError(ValueError):
    """Invalid absolute or epoch-scoped synthetic observation."""


def _finite_position(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1000
    except (OverflowError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class EpochProprioception:
    """A versioned observation with no modulo-wrapped causal timestamp."""

    absolute_tick: int
    epoch_index: int
    epoch_tick: int
    revision: str
    x: float
    y: float
    pulse: bool

    def __post_init__(self):
        if (
            type(self.absolute_tick) is not int
            or not 0 <= self.absolute_tick < MAX_ABSOLUTE_TICKS
            or type(self.epoch_index) is not int
            or self.epoch_index != self.absolute_tick // EPOCH_TICKS
            or type(self.epoch_tick) is not int
            or self.epoch_tick != self.absolute_tick % EPOCH_TICKS
            or type(self.revision) is not str
            or _REVISION.fullmatch(self.revision) is None
            or not _finite_position(self.x)
            or not _finite_position(self.y)
            or type(self.pulse) is not bool
        ):
            raise EpochSenseError("invalid absolute/epoch proprioception")

    @property
    def tick(self):
        """Absolute monotonic logical tick, never an epoch-local offset."""
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
