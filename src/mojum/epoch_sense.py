"""Epoch-aware proprioception (schema /4) for goal-free runs past 9999 ticks.

Additive and isolated: the historical ``/1``-``/3`` schemas and their
``MAX_EPISODE_TICKS`` domain are untouched. The absolute logical tick is
monotonic; (epoch, local_tick) is derived from it, never a silent modulo
of causal state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .local_cell_sense import DevelopmentError, MAX_EPISODE_TICKS, _finite

EPOCH_SENSE_SCHEMA = "mojum.developmental.proprioception/4"
EPOCH_LENGTH = MAX_EPISODE_TICKS      # fixed by the schema; never configurable
MAX_ABSOLUTE_TICK = 2**53 - 1         # keeps JSON/float round-trips exact
TICK_PERIOD_NS = 1_000_000
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def split_tick(absolute_tick):
    """Return ``(epoch, local_tick)`` for a validated absolute tick."""
    if (type(absolute_tick) is not int
            or not 0 <= absolute_tick <= MAX_ABSOLUTE_TICK):
        raise DevelopmentError("invalid absolute tick")
    return divmod(absolute_tick, EPOCH_LENGTH)


def observed_at_ns(absolute_tick):
    """Monotonic clock derived from the absolute tick only."""
    split_tick(absolute_tick)
    return absolute_tick * TICK_PERIOD_NS


@dataclass(frozen=True)
class EpochProprioception:
    absolute_tick: int
    epoch: int
    local_tick: int
    revision: str
    x: float
    y: float
    pulse: bool

    def __post_init__(self):
        try:
            ok = (type(self.epoch) is int and type(self.local_tick) is int
                  and split_tick(self.absolute_tick) == (self.epoch, self.local_tick))
        except DevelopmentError:
            ok = False
        if (not ok or type(self.epoch) is not int
                or type(self.local_tick) is not int
                or type(self.revision) is not str
                or _REVISION.fullmatch(self.revision) is None
                or not _finite(self.x) or not _finite(self.y)
                or not 0 <= self.x <= 1000 or not 0 <= self.y <= 1000
                or type(self.pulse) is not bool):
            raise DevelopmentError("invalid epoch proprioception")

    @classmethod
    def at(cls, absolute_tick, revision, x, y, pulse):
        epoch, local = split_tick(absolute_tick)
        return cls(absolute_tick, epoch, local, revision, x, y, pulse)

    def public(self):
        return {"schema": EPOCH_SENSE_SCHEMA,
                "absolute_tick": self.absolute_tick,
                "epoch": self.epoch, "local_tick": self.local_tick,
                "observed_at_ns": observed_at_ns(self.absolute_tick),
                "world_revision": self.revision,
                "position_xy": [float(self.x), float(self.y)],
                "heartbeat_pulse": self.pulse,
                "source": "synthetic_proprioception_not_geometry"}
