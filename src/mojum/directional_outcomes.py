"""Opt-in world-axis bearing-conditioned measured motion outcomes.

Reuses the bounded, replay-validated range/outcome memory. Direction arrives
as a coarse synthetic *sensor reading*, never an object coordinate, ID,
hidden material, obstacle map, goal, or privileged actuator command.
"""
from __future__ import annotations

import copy

from .sensory_play import ContactAssociations, DIRECTION_SENSE_SCHEMA, SENSE_SCHEMA
from .temporal_outcomes import RangeOutcomeMemory, TemporalOutcomeError, MAX_UPDATES

SCHEMA = "mojum.temporal.relative-bearing-outcome/1"
BEARINGS = ("none", "center", "x+", "x-", "y+", "y-")


def _core(observation):
    """Fail closed on an untrusted directional observation and strip direction."""
    if (type(observation) is not dict
            or set(observation) != {
                "schema", "tick", "world_revision", "visible",
                "reflectance_rgb", "range_bucket_10cm", "source", "bearing_bin",
            }
            or observation["schema"] != DIRECTION_SENSE_SCHEMA):
        raise TemporalOutcomeError("invalid relative-bearing sensory contract")
    bearing = observation["bearing_bin"]
    if type(observation["visible"]) is not bool:
        raise TemporalOutcomeError("invalid relative-bearing visibility")
    if observation["visible"]:
        if type(bearing) is not str or bearing not in BEARINGS[1:]:
            raise TemporalOutcomeError("visible toy lacks valid relative bearing")
    elif bearing is not None:
        raise TemporalOutcomeError("occluded toy leaked relative bearing")
    core = dict(observation)
    del core["bearing_bin"]
    core["schema"] = SENSE_SCHEMA
    # Validate the complete existing numeric cue (including tick, revision
    # and strict invisibility contract) without adding new privileged fields.
    ContactAssociations._key(core)
    return ("none" if bearing is None else bearing), core


class BearingOutcomeMemory:
    """Independent measured range/action histograms for each visible bearing."""

    def __init__(self):
        self.models = {}
        self.updates = 0

    def snapshot(self):
        return {"schema": SCHEMA, "updates": self.updates,
                "models": {bearing: model.snapshot()
                           for bearing, model in self.models.items()}}

    @classmethod
    def from_snapshot(cls, state):
        if (type(state) is not dict
                or set(state) != {"schema", "updates", "models"}
                or state["schema"] != SCHEMA
                or type(state["updates"]) is not int
                or not 0 <= state["updates"] <= MAX_UPDATES
                or type(state["models"]) is not dict
                or len(state["models"]) > len(BEARINGS)):
            raise TemporalOutcomeError("invalid relative-bearing memory snapshot")
        result = cls()
        total = 0
        for bearing, data in state["models"].items():
            if type(bearing) is not str or bearing not in BEARINGS:
                raise TemporalOutcomeError("invalid learned relative bearing")
            model = RangeOutcomeMemory.from_snapshot(data)
            if model.updates < 1:
                raise TemporalOutcomeError("empty learned relative-bearing model")
            result.models[bearing] = model
            total += model.updates
        if total != state["updates"]:
            raise TemporalOutcomeError("inconsistent directional evidence totals")
        result.updates = total
        return result

    def predict(self, before, action):
        bearing, core = _core(before)
        model = self.models.get(bearing)
        return (model or RangeOutcomeMemory()).predict(core, action)

    def observe(self, before, action, after, blocked, prior):
        bearing, cue = _core(before)
        _, future = _core(after)
        if type(blocked) is not bool or prior != self.predict(before, action):
            raise TemporalOutcomeError("forged or stale bearing prediction")
        if self.updates >= MAX_UPDATES:
            raise TemporalOutcomeError("directional evidence budget exhausted")
        model = self.models.get(bearing)
        if model is None:
            model = RangeOutcomeMemory()
        learned = model.observe(cue, action, future, blocked, prior)
        self.models[bearing] = model
        self.updates += 1
        return learned
