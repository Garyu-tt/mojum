"""Pure CPU synthetic near-field temporal transition learner.

An *evaluation-only* model of measured 10-cm range-bucket changes after a
world-fixture 10-cm X displacement. Not a motor controller or a physics law,
and not installed into the canonical organism. It deliberately has no RGB,
toy-ID, hidden softness or scene geometry inputs beyond typed sensor frames.
"""
from __future__ import annotations

import copy
import json
import re

from .sensory_play import ContactAssociations, Toy, ToyScene
from .world_room import Room, advance, initial_state

SCHEMA = "mojum.eval.temporal-range/1"
MAX_UPDATES = 128
MAX_CONTEXTS = 14
_KEY = re.compile(r"[0-6]:[+-]\Z")
_HIST = re.compile(r"[0-6]\Z")
FORWARD_X_M = .10


class TemporalError(ValueError):
    """Unsupported or inconsistent numeric temporal observation."""


def _key(sensor, dx):
    # Reuse original R0.9 strict cue/tick/revision bounds; never use its RGB
    # return value for temporal learning or decisions.
    ContactAssociations._key(sensor)
    if sensor["visible"] is not True:
        raise TemporalError("temporal range model needs a visible numeric cue")
    if type(dx) is not float or dx not in (FORWARD_X_M, -FORWARD_X_M):
        raise TemporalError("only bounded synthetic 10cm X evaluation steps")
    return f'{sensor["range_bucket_10cm"]}:{"+" if dx > 0 else "-"}'


class TemporalRangeModel:
    """Empirical transition frequencies for (range bucket, signed motion)."""

    def __init__(self):
        self.table = {}
        self.updates = 0

    def snapshot(self):
        return {"schema": SCHEMA, "updates": self.updates,
                "transitions": copy.deepcopy(self.table)}

    @classmethod
    def from_snapshot(cls, state):
        if (type(state) is not dict or set(state) !=
                {"schema", "updates", "transitions"} or state["schema"] != SCHEMA
                or type(state["updates"]) is not int
                or not 0 <= state["updates"] <= MAX_UPDATES
                or type(state["transitions"]) is not dict
                or len(state["transitions"]) > MAX_CONTEXTS):
            raise TemporalError("invalid range memory snapshot")
        total = 0
        for key, histogram in state["transitions"].items():
            if (type(key) is not str or _KEY.fullmatch(key) is None
                    or type(histogram) is not dict or not histogram
                    or len(histogram) > 7):
                raise TemporalError("invalid range transition context")
            for bucket, count in histogram.items():
                if (type(bucket) is not str or _HIST.fullmatch(bucket) is None
                        or type(count) is not int or not 1 <= count <= MAX_UPDATES):
                    raise TemporalError("invalid transition histogram")
                total += count
        if total != state["updates"]:
            raise TemporalError("inconsistent measured range evidence")
        obj = cls()
        obj.table = copy.deepcopy(state["transitions"])
        obj.updates = state["updates"]
        return obj

    def predict(self, before, dx):
        key = _key(before, dx)
        counts = self.table.get(key)
        if not counts:
            return {"predicted_next_bucket": None, "observations": 0}
        # Modal observed successor, with stable numeric tie-break. No
        # unobserved-range hallucination or hidden labels.
        successor = min(counts, key=lambda x: (-counts[x], int(x)))
        return {"predicted_next_bucket": int(successor),
                "observations": sum(counts.values())}

    def learn(self, before, dx, after, prior):
        if prior != self.predict(before, dx):
            raise TemporalError("stale or forged temporal prediction")
        ContactAssociations._key(after)
        if (after["visible"] is not True
                or after["tick"] != before["tick"] + 1
                or after["world_revision"] == before["world_revision"]):
            raise TemporalError("stale, invisible, or mismatched next sensor")
        if self.updates >= MAX_UPDATES:
            raise TemporalError("temporal evidence budget reached")
        key = _key(before, dx)
        if key not in self.table and len(self.table) >= MAX_CONTEXTS:
            raise TemporalError("range memory capacity reached")
        bucket = str(after["range_bucket_10cm"])
        counts = self.table.setdefault(key, {})
        counts[bucket] = counts.get(bucket, 0) + 1
        self.updates += 1
        return {"measured_next_bucket": int(bucket),
                "updates": self.updates,
                "prediction_error": (None if prior["predicted_next_bucket"] is None
                                     else int(bucket) - prior["predicted_next_bucket"])}


def measured_transition(*, toy_id, center_x, start_x, rgb, room=None,
                        step=FORWARD_X_M, count=3):
    """Generate real paired ToyScene readings around real point-room advances.

    Benchmark-only displacement is 10cm, deliberately NOT the R0.13
    Aquarium motor-policy's 4cm actuation budget. No physical motor exists.
    """
    room = Room(4., 4.) if room is None else room
    scene = ToyScene(toys=(Toy(toy_id, center_x, 2., .15, rgb, .5),))
    world = initial_state(room, start_x, 2.)
    frames = []
    for _ in range(count):
        before = scene.sense(world)
        updated, feedback = advance(
            room, world, tick=world.tick + 1, dx=step, dy=0.,
            expected_revision=world.revision,
        )
        after = scene.sense(updated)
        frames.append({"before": before, "after": after,
                       "world_blocked": feedback["blocked"]})
        world = updated
    return frames


def _evaluate(model, frozen, frames):
    rows = []
    for pair in frames:
        before, after = pair["before"], pair["after"]
        current = before["range_bucket_10cm"]
        observed = after["range_bucket_10cm"]
        prediction = model.predict(before, FORWARD_X_M)
        # A *frozen* never-trained memory correctly yields no prediction.
        assert frozen.predict(before, FORWARD_X_M)["predicted_next_bucket"] is None
        guess = prediction["predicted_next_bucket"]
        rows.append({
            "prior_range_bucket": current,
            "measured_next_bucket": observed,
            "learned_next_bucket": guess,
            "learned_observation_count": prediction["observations"],
            "learned_squared_error": (None if guess is None
                                     else (guess - observed) ** 2),
            "frozen_model_prediction": None,
            # Explicit persistence predictor, distinct from the frozen model:
            "fixed_persistence_guess": current,
            "fixed_persistence_squared_error": (current - observed) ** 2,
            "world_blocked": pair["world_blocked"],
        })
    return rows


def run_temporal_benchmark():
    """Compare learned numeric transitions with untrained/persistence control."""
    model, frozen = TemporalRangeModel(), TemporalRangeModel()
    training = measured_transition(
        toy_id="training.red", center_x=1.2, start_x=.8,
        rgb=(220, 70, 30),
    )
    for pair in training:
        prior = model.predict(pair["before"], FORWARD_X_M)
        model.learn(pair["before"], FORWARD_X_M, pair["after"], prior)
        frozen.predict(pair["before"], FORWARD_X_M)

    before_eval, frozen_before = model.snapshot(), frozen.snapshot()
    novel_rgb = _evaluate(model, frozen, measured_transition(
        toy_id="heldout.green", center_x=2.8, start_x=2.4,
        rgb=(30, 210, 95),
    ))
    # Same numeric law but a 4-bucket initial context not observed in training.
    shifted = _evaluate(model, frozen, measured_transition(
        toy_id="heldout.orange", center_x=3.2, start_x=2.8,
        rgb=(230, 120, 40),
    ))
    # A previously unseen wall blocks the actual movement. This model
    # has no forward ray or privileged obstacle geometry, so it SHOULD err.
    walled = _evaluate(model, frozen, measured_transition(
        toy_id="heldout.blocked", center_x=2.8, start_x=2.4,
        rgb=(30, 210, 95),
        room=Room(4., 4., ((2.45, 1.95, .01, .10),)),
        count=1,
    ))
    if model.snapshot() != before_eval or frozen.snapshot() != frozen_before:
        raise AssertionError("evaluation must not train models")
    return {
        "schema": SCHEMA,
        "status": "synthetic_range_transitions_not_material_generalization",
        "training_transitions": model.updates,
        "frozen_updates": frozen.updates,
        "training_range_buckets": [p["before"]["range_bucket_10cm"] for p in training],
        "novel_rgb_and_new_position": novel_rgb,
        "novel_initial_distance": shifted,
        "unseen_wall_negative_control": walled,
        "interpretation": (
            "Numeric 10cm range transition learned from red toy predicts an "
            "identical approach on a green toy at a new position. The model "
            "has no learned transition for a new starting bucket and makes a "
            "wrong forecast when an unseen wall blocks the action. Motion "
            "representation deliberately ignores RGB; this is narrow "
            "kinematic transfer, NOT material/object understanding."
        ),
    }


def main():
    print(json.dumps(run_temporal_benchmark(), sort_keys=True, indent=2,
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
