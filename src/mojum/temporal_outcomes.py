"""Opt-in, CPU-only synthetic movement-outcome memory for one Aquarium.

Predicts observed *post-action range buckets AND whether virtual movement
was blocked*, not an unconstrained intended-motion physics rule.
Observations come from actual ToyScene.sense() on committed pre/post
point-room states. No RGB, hidden toy IDs/softness, geometry or actuator IO
is passed to the empirical model.
"""
from __future__ import annotations

import copy
import re

from .developmental_policy import ACTIONS
from .sensory_play import ContactAssociations

SCHEMA = "mojum.temporal.range-outcome/1"
MAX_UPDATES = 9_999
MAX_CONTEXTS = 40
_KEY = re.compile(r"(?:none|[0-6])\|(?:rest|x\+|x-|y\+|y-)\Z", re.ASCII)
_OUTCOME = re.compile(r"(?:none|[0-6])\|[01]\Z", re.ASCII)


class TemporalOutcomeError(ValueError):
    """Invalid causal cue, measurement, model or learned state."""


def _bucket(observation):
    """Validate full numeric sensory contract, then discard RGB and ID."""
    ContactAssociations._key(observation)
    return str(observation["range_bucket_10cm"]) if observation["visible"] else "none"


def _context(observation, action):
    if type(action) is not str or action not in ACTIONS:
        raise TemporalOutcomeError("unknown virtual movement")
    return _bucket(observation) + "|" + action


class RangeOutcomeMemory:
    """Bounded empirical table for requested action -> *measured* outcome."""

    def __init__(self):
        self.histograms = {}
        self.updates = 0

    def snapshot(self):
        return {"schema": SCHEMA, "updates": self.updates,
                "histograms": copy.deepcopy(self.histograms)}

    @classmethod
    def from_snapshot(cls, snapshot):
        if (type(snapshot) is not dict
                or any(type(key) is not str for key in snapshot)
                or set(snapshot) != {"schema", "updates", "histograms"}
                or type(snapshot["schema"]) is not str
                or snapshot["schema"] != SCHEMA
                or type(snapshot["updates"]) is not int
                or not 0 <= snapshot["updates"] <= MAX_UPDATES
                or type(snapshot["histograms"]) is not dict
                or len(snapshot["histograms"]) > MAX_CONTEXTS):
            raise TemporalOutcomeError("invalid movement-outcome snapshot")
        total = 0
        for key, outcomes in snapshot["histograms"].items():
            if (type(key) is not str or _KEY.fullmatch(key) is None
                    or type(outcomes) is not dict or not outcomes
                    or len(outcomes) > 16):
                raise TemporalOutcomeError("invalid movement context")
            for outcome, count in outcomes.items():
                if (type(outcome) is not str or _OUTCOME.fullmatch(outcome) is None
                        or type(count) is not int
                        or not 1 <= count <= MAX_UPDATES):
                    raise TemporalOutcomeError("invalid movement outcome")
                # A blocked point-room movement cannot change range,
                # and a zero-displacement rest is never blocked.
                before_bucket, action = key.split("|")
                next_bucket, blocked_flag = outcome.split("|")
                if blocked_flag == "1" and (
                        before_bucket != next_bucket or action == "rest"):
                    raise TemporalOutcomeError("impossible blocked transition snapshot")
                total += count
        if total != snapshot["updates"]:
            raise TemporalOutcomeError("inconsistent movement evidence count")
        result = cls()
        result.histograms = copy.deepcopy(snapshot["histograms"])
        result.updates = snapshot["updates"]
        return result

    def predict(self, before, action):
        key = _context(before, action)
        outcomes = self.histograms.get(key)
        if not outcomes:
            return {"observations": 0, "next_bucket": None,
                    "blocked": None}
        # Argmax over *paired* observed outcomes. Sorting prevents
        # insertion-order/iteration-order inference differences.
        guess = min(outcomes, key=lambda k: (-outcomes[k], k))
        bucket, blocked = guess.split("|")
        return {"observations": sum(outcomes.values()),
                "next_bucket": None if bucket == "none" else int(bucket),
                "blocked": blocked == "1"}

    def observe(self, before, action, after, blocked, prior):
        # Accept only canonical scalar evidence. Python's overloaded __eq__ or
        # __ne__ on arbitrary objects must not bypass a causal prediction check.
        if (type(blocked) is not bool
                or type(prior) is not dict
                or any(type(key) is not str for key in prior)
                or set(prior) != {"observations", "next_bucket", "blocked"}
                or type(prior["observations"]) is not int
                or (prior["next_bucket"] is not None
                    and type(prior["next_bucket"]) is not int)
                or (prior["blocked"] is not None
                    and type(prior["blocked"]) is not bool)
                or prior != self.predict(before, action)):
            raise TemporalOutcomeError("forged or stale movement prediction")
        b, a = _bucket(before), _bucket(after)
        if (after["tick"] != before["tick"] + 1
                or after["world_revision"] == before["world_revision"]):
            raise TemporalOutcomeError("stale or mismatched post-action sensor")
        # The point-room on a blocked move never changes the current
        # position; therefore its numeric nearest-toy range stays unchanged.
        if blocked and (a != b or action == "rest"):
            raise TemporalOutcomeError("impossible synthetic blocked outcome")
        if self.updates >= MAX_UPDATES:
            raise TemporalOutcomeError("movement evidence limit")
        key = _context(before, action)
        if key not in self.histograms and len(self.histograms) >= MAX_CONTEXTS:
            raise TemporalOutcomeError("movement context limit")
        value = a + "|" + ("1" if blocked else "0")
        counts = self.histograms.setdefault(key, {})
        counts[value] = counts.get(value, 0) + 1
        self.updates += 1
        return {"measured_next_bucket": None if a == "none" else int(a),
                "measured_blocked": blocked,
                "prediction_was_known": prior["observations"] > 0,
                "total_updates": self.updates,
                "source": "measured_after_synthetic_room_step_not_planned_motion"}
