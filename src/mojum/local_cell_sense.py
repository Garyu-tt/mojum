"""Historical typed proprioception shared by the v2A and v2B policies."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

SENSE_SCHEMA = "mojum.developmental.proprioception/1"
ACTIONS = ("rest", "x+", "y+", "x-", "y-")
STEP_METERS = 0.04
MAX_EPISODE_TICKS = 9999
MAX_ZONES = 512
_REVISION = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class DevelopmentError(ValueError):
    """Invalid synthetic observation, model or causal transition."""


def _finite(x):
    if type(x) not in (int, float):
        return False
    try:
        return math.isfinite(x)
    except (OverflowError, ValueError, TypeError):
        # Python raises OverflowError when converting unbounded ints to float.
        return False


@dataclass(frozen=True)
class Proprioception:
    tick: int
    revision: str
    x: float
    y: float
    pulse: bool

    def __post_init__(self):
        if (type(self.tick) is not int or not 0 <= self.tick < MAX_EPISODE_TICKS
                or type(self.revision) is not str
                or _REVISION.fullmatch(self.revision) is None
                or not _finite(self.x) or not _finite(self.y)
                or not 0 <= self.x <= 1000 or not 0 <= self.y <= 1000
                or type(self.pulse) is not bool):
            raise DevelopmentError("invalid numeric proprioception")

    def public(self):
        return {"schema": SENSE_SCHEMA, "tick": self.tick,
                "world_revision": self.revision,
                "position_xy": [float(self.x), float(self.y)],
                "heartbeat_pulse": self.pulse,
                "source": "synthetic_proprioception_not_geometry"}
