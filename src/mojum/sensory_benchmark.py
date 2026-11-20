"""Offline counterfactual benchmark of the existing synthetic contact learner.

Measures *prediction coverage* and squared error on unseen toy instances,
using actual ToyScene.sense/probe results. The frozen model receives the
identical cues but makes no updates. This is not an Aquarium runtime change,
real physical contact, object recognition, or proof of generalization.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from .sensory_play import ContactAssociations, Toy, ToyScene
from .world_room import initial_state

SCHEMA = "mojum.eval.toy-contact-frozen-heldout/1"
FROZEN_FALLBACK = 0.5


@dataclass(frozen=True)
class Trial:
    label: str  # Evaluation-only label; NEVER used by the learner.
    rgb: tuple[int, int, int]
    deflection: float


def _measure(trial):
    """Sample the fixture, never construct a sensory label by hand."""
    scene = ToyScene(toys=(
        Toy(trial.label, 1.20, 2.0, .15, trial.rgb, trial.deflection),
    ))
    state = initial_state(scene.room, 1.0, 2.0)
    sensor = scene.sense(state)
    outcome = scene.probe(state, sensor)
    if not sensor["visible"] or not outcome["contact"]:
        raise AssertionError("benchmark contact fixture must be measurable")
    return sensor, outcome


def _squared_error(prediction, observed):
    return (prediction - observed) ** 2


def run_benchmark():
    """Return deterministic evidence without modifying any real organism.

    The toy IDs are different in every evaluation case. Two repeat-RGB toys
    share the same response as training; a contradictory toy has the same
    RGB but a *different* response; a novel-RGB toy has never been observed.
    The learner is permitted to fail on the latter two cases.
    """
    trained = ContactAssociations()
    frozen = ContactAssociations()
    training_trials = (
        Trial("train.red", (220, 70, 30), .75),
        Trial("train.blue", (30, 130, 220), .20),
    )
    first_predictions = []
    for _ in range(2):
        for spec in training_trials:
            sensor, outcome = _measure(spec)
            before = trained.predict(sensor)
            first_predictions.append(before["predicted_deflection"])
            trained.learn(sensor, outcome, before)
            # Deliberate no-op for frozen control: observe the same inputs
            # but do not permit any evidence update.
            frozen.predict(sensor)

    trained_before = trained.snapshot()
    frozen_before = frozen.snapshot()

    heldout = (
        ("new_instance_same_red", Trial("heldout.red", (220, 70, 30), .75)),
        ("new_instance_same_blue", Trial("heldout.blue", (30, 130, 220), .20)),
        ("contradictory_red", Trial("heldout.contradiction", (220, 70, 30), .10)),
        ("new_unseen_green", Trial("heldout.green", (30, 210, 95), .40)),
    )

    rows = []
    for group, spec in heldout:
        sensor, outcome = _measure(spec)
        learned = trained.predict(sensor)
        frozen_prediction = frozen.predict(sensor)["predicted_deflection"]
        if frozen_prediction is not None:
            raise AssertionError("frozen untrained predictor gained knowledge")
        observed = outcome["measured_deflection"]
        predicted = learned["predicted_deflection"]
        rows.append({
            "group": group,
            "cue_rgb": sensor["reflectance_rgb"],
            "range_bucket_10cm": sensor["range_bucket_10cm"],
            "measured_contact_deflection": observed,
            "learned_prediction": predicted,
            "learned_coverage": predicted is not None,
            "learned_squared_error": (
                _squared_error(predicted, observed)
                if predicted is not None else None
            ),
            # The untrained model truly predicts None. Its explicit fixed
            # prior is the comparison score, not a fabricated model output.
            "frozen_model_prediction": None,
            "frozen_default_prior": FROZEN_FALLBACK,
            "frozen_default_squared_error": (
                _squared_error(FROZEN_FALLBACK, observed)
            ),
        })

    if trained.snapshot() != trained_before or frozen.snapshot() != frozen_before:
        raise AssertionError("held-out evaluation mutated a model")

    return {
        "schema": SCHEMA,
        "scope": "deterministic_synthetic_numeric_probes_only",
        "training_probes": len(first_predictions),
        "initial_predictions": first_predictions[:2],
        "learned_model_updates": trained.updates,
        "frozen_model_updates": frozen.updates,
        "same_cue_different_hidden_response_possible": True,
        "heldout_trials": rows,
        "interpretation": (
            "RGB exact-match memory transfers to new IDs with matching cues; "
            "it may be wrong when same RGB hides a different response and "
            "makes no prediction for a genuinely unseen RGB cue."
        ),
    }


def main():
    print(json.dumps(run_benchmark(), sort_keys=True, indent=2,
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
