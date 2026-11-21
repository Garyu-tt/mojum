"""R0.14 frozen held-out directional transfer; deterministic synthetic QA only.

This benchmark does NOT modify the Aquarium, train on test scenes, use
privileged future geometry in a predictor, or call a real actuator. It
collects actual pre/post numeric samples from ToyScene + Room.advance,
then scores frozen empirical memories on distinctly constructed scenes.
"""
from __future__ import annotations

import json

from .developmental_policy import STEP_METERS
from .directional_outcomes import BearingOutcomeMemory
from .sensory_play import SENSE_SCHEMA, Toy, ToyScene
from .temporal_outcomes import RangeOutcomeMemory
from .world_room import Room, advance, initial_state

SCHEMA = "mojum.eval.frozen-bearing-transfer/1"
ACTION = "x+"
START = (1.0, 2.0)


def _measured_case(name, *, x, y=2.0, start=START,
                   rgb=(210, 35, 15), softness=.3, room=None):
    """Fixture truth is produced by a real point-room action, not imagined."""
    if room is None:
        room = Room(4.0, 4.0)
    scene = ToyScene(
        width=room.width, height=room.height,
        toys=(Toy("spectator.fixture." + name, x, y, .1, rgb, softness),),
    )
    current = initial_state(room, *start)
    before = scene.sense_direction(current, occluding_obstacles=room.obstacles)
    before_numeric = scene.sense(current, occluding_obstacles=room.obstacles)
    future, motion = advance(
        room, current, tick=1, dx=STEP_METERS, dy=0.0,
        expected_revision=current.revision,
    )
    after = scene.sense_direction(future, occluding_obstacles=room.obstacles)
    after_numeric = scene.sense(future, occluding_obstacles=room.obstacles)
    # The extended sensor is an additional measured cue; the canonical
    # numeric cue and occlusion must be exactly the same. No toy name or
    # privileged geometry is passed to the empirical learners.
    for directional, numeric in ((before, before_numeric),
                                 (after, after_numeric)):
        reconstructed = dict(directional)
        reconstructed.pop("bearing_bin")
        reconstructed["schema"] = SENSE_SCHEMA
        if reconstructed != numeric:
            raise AssertionError("directional sensor changed numeric sensor")
    return {
        "name": name, "before": before, "after": after,
        "before_numeric": before_numeric, "after_numeric": after_numeric,
        "blocked": motion["blocked"],
        "applied_dx": future.x - current.x,
        "requested_dx": STEP_METERS,
    }


def _predict(models, record):
    return {
        "numeric_only": models["numeric_only"].predict(
            record["before_numeric"], ACTION),
        "bearing": models["bearing"].predict(record["before"], ACTION),
        "frozen_untrained": BearingOutcomeMemory().predict(
            record["before"], ACTION),
    }


def _score(rows, key):
    correct = wrong = abstained = 0
    for row in rows:
        p = row["predictions"][key]
        if p["blocked"] is None:
            abstained += 1
        elif (p["blocked"] == row["actual_blocked"]
              and p["next_bucket"] == row["actual_next_bucket"]):
            correct += 1
        else:
            wrong += 1
    return {
        "correct_pairs": correct,
        "wrong_pairs": wrong,
        "abstained": abstained,
        "answered": correct + wrong,
        "total": len(rows),
    }


def evaluate():
    """A/B against a numeric-only model and an explicitly frozen empty model.

    Two training experiences from mirrored scenes share the exact same
    numeric distance/action context. Their measurable next buckets differ.
    All seven tests are evaluated without mutating either trained memory.
    """
    training = (
        _measured_case("train.east", x=1.22, rgb=(220, 30, 40)),
        _measured_case("train.west", x=.78, rgb=(40, 70, 220)),
    )
    models = {"numeric_only": RangeOutcomeMemory(),
              "bearing": BearingOutcomeMemory()}
    for event in training:
        models["numeric_only"].observe(
            event["before_numeric"], ACTION, event["after_numeric"],
            event["blocked"],
            models["numeric_only"].predict(event["before_numeric"], ACTION),
        )
        models["bearing"].observe(
            event["before"], ACTION, event["after"], event["blocked"],
            models["bearing"].predict(event["before"], ACTION),
        )

    # Distinct absolute coordinates, scene sizes, RGB and hidden softness.
    # The translated room contains a new remote obstacle irrelevant to x+.
    remote_wall = Room(5., 4., ((3., 1., .1, 2.),))
    near_wall = Room(4., 4., ((1.01, 1.96, .01, .08),))
    held_out = (
        _measured_case("east.translated.new_room", x=1.62, y=2.1,
                       start=(1.4, 2.1), rgb=(3, 210, 99),
                       softness=.98, room=remote_wall),
        _measured_case("west.translated.new_room", x=1.18, y=2.1,
                       start=(1.4, 2.1), rgb=(21, 170, 56),
                       softness=.04, room=remote_wall),
        _measured_case("east.new_rgb_and_material", x=1.22,
                       rgb=(9, 188, 117), softness=.9),
        _measured_case("east.same_bearing_farther", x=1.26,
                       rgb=(45, 190, 80), softness=.11),
        _measured_case("west.unseen_wall", x=.78,
                       rgb=(80, 33, 222), room=near_wall),
        _measured_case("north.untrained_bearing", x=1., y=2.22,
                       rgb=(30, 80, 140)),
        _measured_case("east.occluded", x=1.22,
                       rgb=(220, 40, 90), room=near_wall),
    )
    before = {key: model.snapshot() for key, model in models.items()}
    rows = []
    for case in held_out:
        rows.append({
            "case": case["name"],
            "initial_bucket": case["before"]["range_bucket_10cm"],
            "observed_bearing": case["before"]["bearing_bin"],
            "requested_dx": case["requested_dx"],
            "applied_dx": case["applied_dx"],
            "actual_next_bucket": case["after"]["range_bucket_10cm"],
            "actual_blocked": case["blocked"],
            "predictions": _predict(models, case),
        })
    if before != {key: model.snapshot() for key, model in models.items()}:
        raise AssertionError("held-out evaluation mutated trained memory")
    if any(model.updates != len(training) for model in models.values()):
        raise AssertionError("training count changed during evaluation")
    result = {
        "schema": SCHEMA,
        "kind": "deterministic_2d_synthetic_frozen_heldout_not_general_physics",
        "action": ACTION, "step_m": STEP_METERS,
        "train_count": len(training), "test_count": len(rows),
        "models": {
            "numeric_only": _score(rows, "numeric_only"),
            "bearing": _score(rows, "bearing"),
            "frozen_untrained": _score(rows, "frozen_untrained"),
        },
        "samples": rows,
        "interpretation": (
            "Bearing separates mirrored numeric contexts but still makes "
            "false predictions for unseen distances and walls; abstention "
            "is not a prediction, and fixture outcomes are not physical laws."
        ),
    }
    return result


def main():
    print(json.dumps(evaluate(), sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
