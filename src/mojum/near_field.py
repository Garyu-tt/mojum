"""Opt-in CPU-only four-ray numeric proximity sensing and experience memory.

A synthetic room's geometry is only used by the *environment* to generate
four bounded distances (east/west/north/south). It is never supplied to the
learner. Rays are virtual measurements, not cameras, real proximity devices,
contact physics, or a physical motor-control authority.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
import re

from .developmental_policy import (
    DevelopmentError, Explorer, Proprioception, MAX_EPISODE_TICKS, MAX_ZONES, _finite,
)
from .world_room import Room, RoomState

SENSE_SCHEMA = "mojum.sensor.synthetic-room-rays/1"
MODEL_SCHEMA = "mojum.developmental.room-ray-affordance/1"
ACTIONS = ("x+", "x-", "y+", "y-", "rest")
MAX_BUCKET = 12
BUCKET_M = 0.05
STEP_METERS = 0.04
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CONTEXT = re.compile(
    r"(?:0|[1-9][0-9]{0,3}):(?:0|[1-9][0-9]{0,3})"
    r"\|(?:0|[1-9]|1[0-2]),(?:0|[1-9]|1[0-2]),"
    r"(?:0|[1-9]|1[0-2]),(?:0|[1-9]|1[0-2])\Z", re.ASCII,
)


def sense_room_rays(room, state):
    """Quantize nearest axis-aligned walls, without revealing room geometry.

    Four integer buckets count 5 cm increments capped at 60 cm, in
    E/W/N/S order. The world owns this calculation; a learner sees only
    a numeric result for the *committed current* state.
    """
    if type(room) is not Room or type(state) is not RoomState:
        raise DevelopmentError("trusted point-room and state required")
    x, y = state.x, state.y
    if (type(state.tick) is not int or state.tick < 0
            or type(state.revision) is not str
            or _SHA.fullmatch(state.revision) is None
            or not all(_finite(v) for v in (x, y))
            or not 0 <= x <= room.width or not 0 <= y <= room.height):
        raise DevelopmentError("invalid virtual world state")
    distances = [room.width - x, x, room.height - y, y]
    for a, b, w, h in room.obstacles:
        if a <= x <= a + w and b <= y <= b + h:
            raise DevelopmentError("sensor origin inside fixture obstacle")
        if b <= y <= b + h:
            if x <= a:
                distances[0] = min(distances[0], a - x)
            if x >= a + w:
                distances[1] = min(distances[1], x - (a + w))
        if a <= x <= a + w:
            if y <= b:
                distances[2] = min(distances[2], b - y)
            if y >= b + h:
                distances[3] = min(distances[3], y - (b + h))
    return tuple(min(MAX_BUCKET, max(0, int(max(0.0, d) / BUCKET_M + 1e-10)))
                 for d in distances)


@dataclass(frozen=True)
class RangeSignal(Proprioception):
    rays: tuple[int, int, int, int]

    def __post_init__(self):
        super().__post_init__()
        if (type(self.rays) is not tuple or len(self.rays) != 4
                or any(type(v) is not int or not 0 <= v <= MAX_BUCKET
                       for v in self.rays)):
            raise DevelopmentError("invalid numeric room-ray signal")

    def public(self):
        return {
            "schema": SENSE_SCHEMA,
            "tick": self.tick,
            "world_revision": self.revision,
            "position_xy": [float(self.x), float(self.y)],
            "heartbeat_pulse": self.pulse,
            "range_bucket_5cm_ewns": list(self.rays),
            "source": "synthetic_axis_ranges_not_geometry_or_camera",
        }


class RangeExplorer(Explorer):
    """Goal-free motor play; empirical outcomes keyed by numeric range context.

    Unlike the birth-time action rotation, learned successes/blocked attempts
    change preferences within an identical numeric sensor context. There is
    no reward goal, object ID, pretrained meaning, or world geometry in state.
    """

    @staticmethod
    def _zone(signal):
        return (f"{int(signal.x * 2)}:{int(signal.y * 2)}|"
                + ",".join(str(n) for n in signal.rays))

    def snapshot(self):
        state = super().snapshot()
        state["schema"] = MODEL_SCHEMA
        return state

    @classmethod
    def from_snapshot(cls, state):
        if (type(state) is not dict
                or any(type(key) is not str for key in state)
                or set(state) !=
                {"schema", "last_tick", "updates", "records"}
                or type(state["schema"]) is not str
                or state["schema"] != MODEL_SCHEMA
                or type(state["last_tick"]) is not int
                or not -1 <= state["last_tick"] < MAX_EPISODE_TICKS
                or type(state["updates"]) is not int
                or state["updates"] != state["last_tick"] + 1
                or type(state["records"]) is not dict
                or len(state["records"]) > MAX_ZONES):
            raise DevelopmentError("invalid range learner snapshot")
        total = 0
        for context, actions in state["records"].items():
            if (type(context) is not str or _CONTEXT.fullmatch(context) is None
                    or type(actions) is not dict or not actions
                    or any(type(key) is not str for key in actions)
                    or not set(actions).issubset(ACTIONS)):
                raise DevelopmentError("invalid numeric ray context")
            for counts in actions.values():
                if (type(counts) is not list or len(counts) != 2
                        or any(type(v) is not int for v in counts)
                        or not 1 <= counts[0] <= MAX_EPISODE_TICKS
                        or not 0 <= counts[1] <= counts[0]):
                    raise DevelopmentError("invalid measured affordance counts")
                total += counts[0]
        if total != state["updates"]:
            raise DevelopmentError("inconsistent range evidence")
        learner = cls()
        learner.last_tick = state["last_tick"]
        learner.updates = state["updates"]
        learner.records = copy.deepcopy(state["records"])
        return learner

    def decide(self, signal):
        if type(signal) is not RangeSignal or signal.tick != self.last_tick + 1:
            raise DevelopmentError("stale or invalid range-sensory tick")
        context = self._zone(signal)
        learned = self.records.get(context, {})
        rotation = (signal.tick + int(signal.pulse)) % len(ACTIONS)
        order = ACTIONS[rotation:] + ACTIONS[:rotation]

        def empirical_score(action):
            observed, blocked = learned.get(action, [0, 0])
            # Known successful movement > unseen alternative > empirically
            # blocked action. This is bounded local evidence, not planning.
            if blocked:
                return -1
            return 1 if observed else 0

        action = max(order, key=empirical_score)
        steps = {"rest": (0.0, 0.0), "x+": (STEP_METERS, 0.0),
                 "x-": (-STEP_METERS, 0.0), "y+": (0.0, STEP_METERS),
                 "y-": (0.0, -STEP_METERS)}
        dx, dy = steps[action]
        return {"action": action, "dx": dx, "dy": dy,
                "expected_revision": signal.revision, "tick": signal.tick,
                "zone": context, "source": "bounded_numeric_room_ray_play"}
