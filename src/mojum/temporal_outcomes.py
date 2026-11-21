"""Opt-in, CPU-only synthetic movement-outcome memory for one Aquarium.

Predicts observed *post-action range buckets AND whether virtual movement
was blocked*, not an unconstrained intended-motion physics rule.
Observations come from actual ToyScene.sense() on committed pre/post
point-room states. No RGB, hidden toy IDs/softness, geometry or actuator IO
is passed to the empirical model.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import re

from .developmental import MOVES, REST

# This policy uses MotorPlay's 0.125 m actions. The experimental #90 policy
# used a different action vocabulary and 0.04 m steps.
SCHEMA = "mojum.temporal.motor-play-range/1"
GUIDANCE_SCHEMA = "mojum.developmental.range-guidance/1"
ACTIONS = tuple(move[0] for move in (*MOVES, REST))
MAX_UPDATES = 9_999
MAX_CONTEXTS = 40
_KEY = re.compile(r"(?:none|[0-6])\|(?:rest|east|north|west|south)\Z", re.ASCII)
_OUTCOME = re.compile(r"(?:none|[0-6])\|[01]\Z", re.ASCII)


class TemporalOutcomeError(ValueError):
    """Invalid causal cue, measurement, model or learned state."""


@dataclass(frozen=True)
class RangeCue:
    """Only the measured bucket and the sample's time/revision enter the learner."""

    tick: int
    world_revision: str
    range_bucket_10cm: int | None

    def __post_init__(self):
        bucket = self.range_bucket_10cm
        if (type(self.tick) is not int or self.tick < 0
                or type(self.world_revision) is not str
                or len(self.world_revision) != 64
                or any(c not in "0123456789abcdef" for c in self.world_revision)
                or (bucket is not None and (type(bucket) is not int or not 0 <= bucket <= 6))):
            raise TemporalOutcomeError("invalid measured range cue")


def _bucket(cue):
    if type(cue) is not RangeCue:
        raise TemporalOutcomeError("measured range cue required")
    return "none" if cue.range_bucket_10cm is None else str(cue.range_bucket_10cm)


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
                or set(snapshot) != {"schema", "updates", "histograms"}
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
            before_bucket, action = key.split("|")
            resting = action == "rest"
            for outcome, count in outcomes.items():
                if (type(outcome) is not str or _OUTCOME.fullmatch(outcome) is None
                        or type(count) is not int
                        or not 1 <= count <= MAX_UPDATES):
                    raise TemporalOutcomeError("invalid movement outcome")
                next_bucket, blocked_flag = outcome.split("|")
                blocked = blocked_flag == "1"
                if (blocked and resting) or ((blocked or resting) and before_bucket != next_bucket):
                    raise TemporalOutcomeError("impossible stationary transition snapshot")
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
        # Do not invent certainty from a lexical tie-break. Two scenes may
        # produce identical coarse range/action cues but contradictory outcomes.
        # A tied empirical mode must abstain; guided motion then falls back
        # to bounded exploration rather than treating arbitrary key order
        # as evidence of safety or useful progress.
        maximum = max(outcomes.values())
        winners = [key for key, count in outcomes.items() if count == maximum]
        total = sum(outcomes.values())
        if len(winners) != 1:
            return {"observations": total, "next_bucket": None,
                    "blocked": None}
        bucket, blocked = winners[0].split("|")
        return {"observations": total,
                "next_bucket": None if bucket == "none" else int(bucket),
                "blocked": blocked == "1"}

    def observe(self, before, action, after, blocked, prior):
        if type(blocked) is not bool or prior != self.predict(before, action):
            raise TemporalOutcomeError("forged or stale movement prediction")
        before_bucket, after_bucket = _bucket(before), _bucket(after)
        if (after.tick != before.tick + 1
                or after.world_revision == before.world_revision):
            raise TemporalOutcomeError("stale or mismatched post-action sensor")
        # Rest and blocked moves leave position and range unchanged.
        resting = action == "rest"
        if (blocked and resting) or ((blocked or resting) and before_bucket != after_bucket):
            raise TemporalOutcomeError("impossible synthetic stationary outcome")
        if self.updates >= MAX_UPDATES:
            raise TemporalOutcomeError("movement evidence limit")
        key = _context(before, action)
        if key not in self.histograms and len(self.histograms) >= MAX_CONTEXTS:
            raise TemporalOutcomeError("movement context limit")
        value = after_bucket + "|" + ("1" if blocked else "0")
        counts = self.histograms.setdefault(key, {})
        counts[value] = counts.get(value, 0) + 1
        self.updates += 1
        return {"measured_next_bucket": after.range_bucket_10cm,
                "measured_blocked": blocked,
                "prediction_was_known": prior["blocked"] is not None,
                "total_updates": self.updates,
                "source": "measured_after_synthetic_room_step_not_planned_motion"}
