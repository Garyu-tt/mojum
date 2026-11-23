"""Hermetic contract and causality tests for the PUBLIC_SYNTHETIC pulse audit."""

import copy
import hashlib
import io
import json
import math
import unittest
from contextlib import redirect_stdout

from mojum.neural import PulsePredictor
from mojum.pulse_holdout import (
    CLASSIFICATION, MAX_FRAMES, MAX_TIMESTAMP_NS, SCHEMA,
    PulseHoldoutError, evaluate_holdout, main, synthetic_frames,
)


class PulseHoldoutTests(unittest.TestCase):
    def setUp(self):
        self.frames = synthetic_frames(24)

    def run_audit(self, frames=None, **kwargs):
        return evaluate_holdout(
            self.frames if frames is None else frames,
            train_frames=kwargs.pop("train_frames", 12),
            classification=kwargs.pop("classification", CLASSIFICATION),
            **kwargs,
        )

    def test_deterministic_bounded_public_report(self):
        first = self.run_audit()
        self.assertEqual(first, self.run_audit())
        self.assertEqual(first["schema"], SCHEMA)
        self.assertEqual(first["classification"], "PUBLIC_SYNTHETIC")
        self.assertEqual(first["frame_count"], 24)
        self.assertEqual(first["train_frames"], 12)
        self.assertEqual(first["holdout_frames"], 12)
        self.assertEqual(first["training_updates"], 11)
        for name in ("train_online_mse", "frozen_holdout_mse",
                     "causal_previous_pulse_mse"):
            self.assertTrue(math.isfinite(first[name]))
            self.assertGreaterEqual(first[name], 0.0)
        self.assertNotIn("events", first)
        self.assertNotIn("genome_id", first)
        json.dumps(first, allow_nan=False)

    def test_frozen_digest_is_only_training_prefix(self):
        model = PulsePredictor(summation="ordered")
        for frame in self.frames[:12]:
            model.observe(frame)
        snap = model.snapshot()
        encoded = json.dumps(snap, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode("ascii")).hexdigest()
        self.assertEqual(self.run_audit()["frozen_model_sha256"], digest)
        self.assertEqual(snap["updates"], 11)

    def test_no_holdout_label_or_future_feature_leaks_into_first_prediction(self):
        first = self.run_audit()
        altered = copy.deepcopy(self.frames)
        for index in range(12, len(altered)):
            altered[index]["heartbeat"]["pulse"] = (
                not altered[index]["heartbeat"]["pulse"]
            )
            altered[index]["heartbeat"]["phase_milli"] = 999
        other = self.run_audit(altered)
        self.assertEqual(first["first_holdout_prediction"],
                         other["first_holdout_prediction"])
        self.assertEqual(first["first_holdout_baseline"],
                         other["first_holdout_baseline"])
        self.assertEqual(first["frozen_model_sha256"],
                         other["frozen_model_sha256"])
        self.assertNotEqual(first["synthetic_input_sha256"],
                            other["synthetic_input_sha256"])

    def test_first_holdout_label_is_not_used_to_predict_itself(self):
        original = self.run_audit()
        altered = copy.deepcopy(self.frames)
        altered[12]["heartbeat"]["pulse"] = (
            not altered[12]["heartbeat"]["pulse"]
        )
        self.assertEqual(
            original["first_holdout_prediction"],
            self.run_audit(altered)["first_holdout_prediction"],
        )

    def test_one_label_holdout_and_compensated_mode(self):
        result = self.run_audit(self.frames[:3], train_frames=2,
                                summation="compensated")
        self.assertEqual(result["holdout_frames"], 1)
        self.assertEqual(result["training_updates"], 1)
        self.assertEqual(result["summation"], "compensated")

    def test_accepts_detached_tuple_but_not_generator(self):
        self.assertEqual(self.run_audit(tuple(self.frames)),
                         self.run_audit())
        with self.assertRaises(PulseHoldoutError):
            self.run_audit(iter(self.frames))

    def test_rejects_private_or_missing_classification(self):
        for tag in (None, "PRIVATE", "PUBLIC", True, "PUBLIC_SYNTHETIC "):
            with self.subTest(tag=tag), self.assertRaises(PulseHoldoutError):
                self.run_audit(classification=tag)

    def test_rejects_duplicate_backward_and_oversized_timestamps(self):
        for index, timestamp in ((5, self.frames[4]["observed_at_ns"]),
                                 (5, 0), (5, -1),
                                 (5, MAX_TIMESTAMP_NS + 1),
                                 (5, True), (5, 2.5)):
            altered = copy.deepcopy(self.frames)
            altered[index]["observed_at_ns"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(PulseHoldoutError):
                self.run_audit(altered)

    def test_rejects_phase_pulse_and_wire_type_forgeries(self):
        for key, bad in (("phase_milli", -1), ("phase_milli", 1000),
                         ("phase_milli", True), ("phase_milli", 0.5),
                         ("pulse", 1), ("pulse", "true")):
            altered = copy.deepcopy(self.frames)
            altered[15]["heartbeat"][key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(PulseHoldoutError):
                self.run_audit(altered)

    def test_rejects_surplus_world_privilege_and_missing_fields(self):
        altered = copy.deepcopy(self.frames)
        altered[20]["room_geometry"] = {"hidden": True}
        with self.assertRaises(PulseHoldoutError):
            self.run_audit(altered)
        altered = copy.deepcopy(self.frames)
        altered[19]["heartbeat"]["hidden_toy_id"] = "spectator"
        with self.assertRaises(PulseHoldoutError):
            self.run_audit(altered)
        altered = copy.deepcopy(self.frames)
        del altered[22]["heartbeat"]["pulse"]
        with self.assertRaises(PulseHoldoutError):
            self.run_audit(altered)
        altered = copy.deepcopy(self.frames)
        altered[10]["kind"] = "proprioception"
        with self.assertRaises(PulseHoldoutError):
            self.run_audit(altered)

    def test_rejects_oversized_and_too_short_corpora(self):
        for frames in ([], self.frames[:2], self.frames * 86,
                       [None] * (MAX_FRAMES + 1)):
            with self.assertRaises(PulseHoldoutError):
                self.run_audit(frames)
        with self.assertRaises(PulseHoldoutError):
            synthetic_frames(MAX_FRAMES + 1)

    def test_rejects_bad_splits_rates_and_modes(self):
        for split in (0, 1, 24, 25, True, 2.0):
            with self.subTest(split=split), self.assertRaises(PulseHoldoutError):
                self.run_audit(train_frames=split)
        for rate in (True, 0, -0.1, 0.6, float("nan"), float("inf"), "0.1"):
            with self.subTest(rate=rate), self.assertRaises(PulseHoldoutError):
                self.run_audit(learning_rate=rate)
        for mode in ("native", "sum", None, True):
            with self.subTest(mode=mode), self.assertRaises(PulseHoldoutError):
                self.run_audit(summation=mode)

    def test_cli_reproducible_offline_fixture(self):
        output = io.StringIO()
        with redirect_stdout(output):
            returned = main(["--train", "8", "--holdout", "4"])
        self.assertEqual(json.loads(output.getvalue()), returned)
        self.assertEqual(returned["holdout_frames"], 4)
        self.assertEqual(returned["train_frames"], 8)


if __name__ == "__main__":
    unittest.main()
