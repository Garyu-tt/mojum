"""Bounded held-out audit of the synthetic delayed-pulse predictor.

No PRIVATE journal, online service, geometry, real heartbeat measurement,
actuation or claim of organism/neural competence.  Trains only on the declared
prefix, freezes the online model, then evaluates genuinely unseen labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math

from .neural import PulsePredictor

SCHEMA = "mojum.neural.pulse-holdout/1"
CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_FRAMES = 2048
MAX_TIMESTAMP_NS = (1 << 63) - 1
_ALLOWED_FRAME = frozenset({"kind", "observed_at_ns", "heartbeat"})
_ALLOWED_HEARTBEAT = frozenset({"phase_milli", "pulse"})


class PulseHoldoutError(ValueError):
    """Invalid, untrusted or non-synthetic hold-out experiment."""


def _exact_keys(obj, keys):
    return (
        type(obj) is dict
        and len(obj) == len(keys)
        and all(type(key) is str for key in obj)
        and set(obj) == keys
    )


def _validate_frames(frames, classification):
    if type(classification) is not str or classification != CLASSIFICATION:
        raise PulseHoldoutError("PUBLIC_SYNTHETIC classification required")
    if type(frames) not in (tuple, list) or not 3 <= len(frames) <= MAX_FRAMES:
        raise PulseHoldoutError("frames must be a bounded list/tuple of 3..2048")
    normalized = []
    previous_ns = -1
    for frame in frames:
        if not _exact_keys(frame, _ALLOWED_FRAME) or type(frame["kind"]) is not str:
            raise PulseHoldoutError("frame must have exact interoception fields")
        if frame["kind"] != "interoception":
            raise PulseHoldoutError("only synthetic interoception is accepted")
        at = frame["observed_at_ns"]
        heartbeat = frame["heartbeat"]
        if type(at) is not int or not previous_ns < at <= MAX_TIMESTAMP_NS:
            raise PulseHoldoutError("timestamps must be increasing uint63 nanoseconds")
        if not _exact_keys(heartbeat, _ALLOWED_HEARTBEAT):
            raise PulseHoldoutError("unexpected heartbeat fields")
        phase, pulse = heartbeat["phase_milli"], heartbeat["pulse"]
        if type(phase) is not int or not 0 <= phase < 1000 or type(pulse) is not bool:
            raise PulseHoldoutError("invalid synthetic phase or pulse")
        normalized.append((at, phase, pulse))
        previous_ns = at
    return normalized


def _digest(value):
    wire = json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(wire.encode("ascii")).hexdigest()


def evaluate_holdout(frames, *, train_frames, classification,
                     learning_rate=0.1, summation="ordered"):
    """Freeze after train_frames and score each later pulse from PRIOR cues.

    A delayed label at index i uses only frame i-1's phase and pulse.  The
    classifier never sees held-out labels during training, never fits on the
    evaluation suffix, and never calls observe() during the held-out pass.
    """
    normalized = _validate_frames(frames, classification)
    if (type(train_frames) is not int
            or not 2 <= train_frames < len(normalized)):
        raise PulseHoldoutError("train_frames must leave a nonempty holdout")
    if (type(learning_rate) not in (int, float)
            or not math.isfinite(learning_rate)
            or not 0.0 < learning_rate <= 0.5):
        raise PulseHoldoutError("invalid bounded learning rate")
    if type(summation) is not str or summation not in ("ordered", "compensated"):
        raise PulseHoldoutError("explicit summation mode required")

    model = PulsePredictor(float(learning_rate), summation=summation)
    for at, phase, pulse in normalized[:train_frames]:
        model.observe({
            "kind": "interoception", "observed_at_ns": at,
            "heartbeat": {"phase_milli": phase, "pulse": pulse},
        })
    frozen = model.snapshot()
    if frozen["updates"] != train_frames - 1:
        raise PulseHoldoutError("unexpected training update count")
    frozen_digest = _digest(frozen)
    trained_loss = frozen["loss_sum"] / frozen["updates"]

    predictor_errors = []
    baseline_errors = []
    first_prediction = first_baseline = None
    for idx in range(train_frames, len(normalized)):
        _, previous_phase, previous_pulse = normalized[idx - 1]
        _, _, actual_pulse = normalized[idx]
        # This is the same bounded prediction method used by PulsePredictor.
        # It does not update weights; the validation above strips other cues.
        prediction = model._estimate([
            1.0, previous_phase / 1000.0, float(previous_pulse),
        ])
        baseline = float(previous_pulse)
        label = float(actual_pulse)
        if first_prediction is None:
            first_prediction, first_baseline = prediction, baseline
        predictor_errors.append((label - prediction) ** 2)
        baseline_errors.append((label - baseline) ** 2)
    if frozen_digest != _digest(model.snapshot()):
        raise PulseHoldoutError("evaluation unexpectedly mutated frozen model")
    heldout_mse = math.fsum(predictor_errors) / len(predictor_errors)
    baseline_mse = math.fsum(baseline_errors) / len(baseline_errors)
    return {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "frame_count": len(normalized),
        "train_frames": train_frames,
        "holdout_frames": len(normalized) - train_frames,
        "training_updates": frozen["updates"],
        "summation": summation,
        "learning_rate": float(learning_rate),
        "train_online_mse": trained_loss,
        "frozen_holdout_mse": heldout_mse,
        "causal_previous_pulse_mse": baseline_mse,
        "baseline_minus_model_mse": baseline_mse - heldout_mse,
        "first_holdout_prediction": first_prediction,
        "first_holdout_baseline": first_baseline,
        "frozen_model_sha256": frozen_digest,
        "synthetic_input_sha256": _digest(normalized),
        "interpretation": "synthetic_delayed_one_step_prediction_not_biology",
    }


def synthetic_frames(count):
    """Reproducible PUBLIC_SYNTHETIC toy generator, not clinical physiology."""
    if type(count) is not int or not 3 <= count <= MAX_FRAMES:
        raise PulseHoldoutError("invalid synthetic frame count")
    return [
        {
            "kind": "interoception",
            "observed_at_ns": i * 1_000_000,
            "heartbeat": {
                "phase_milli": (i * 137) % 1000,
                "pulse": (i * 137) % 1000 < 250,
            },
        }
        for i in range(count)
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=int, default=64)
    parser.add_argument("--holdout", type=int, default=32)
    parser.add_argument("--summation", choices=("ordered", "compensated"),
                        default="ordered")
    args = parser.parse_args(argv)
    try:
        report = evaluate_holdout(
            synthetic_frames(args.train + args.holdout),
            train_frames=args.train,
            classification=CLASSIFICATION,
            summation=args.summation,
        )
    except PulseHoldoutError as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return report


if __name__ == "__main__":
    main()
