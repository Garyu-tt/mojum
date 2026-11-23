"""Hermetic CPU-only regression suite for RoboPianist keyscore #700(b)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from mojum.robopianist_keyscore import (
    KEYS, SCHEMA, PianoBenchmarkError, demo_trace,
    from_robopianist_arrays, read_trace, score_trace,
)


class RoboPianistKeyscoreTests(unittest.TestCase):
    def test_synthetic_replay_and_frozen_silent_control(self):
        data = demo_trace()
        original = copy.deepcopy(data)
        one, two = score_trace(data), score_trace(data)
        self.assertEqual(one, two)
        self.assertEqual(data, original)
        self.assertEqual(one["key_tp"], 4)
        self.assertEqual(one["key_fn"], 1)
        self.assertEqual(one["key_fp"], 0)
        self.assertAlmostEqual(one["key_micro_f1"], 8 / 9)
        self.assertAlmostEqual(one["exact_frame_rate"], 0.75)
        self.assertAlmostEqual(one["silent_control_exact_frame_rate"], 0.25)
        self.assertGreater(one["exact_delta_vs_silent"], 0)
        self.assertFalse(one["training_or_real_physics_verified"])

    def test_actual_upstream_shaped_arrays_export_without_dependency(self):
        goal = [0.] * (KEYS + 1) * 2
        goal[3] = goal[8] = 1.
        goal[KEYS] = 1
        goal[KEYS + 1 + 17] = 1  # future target not scored
        activation = [0.] * KEYS
        activation[3] = 0.99
        activation[8] = 0.5
        frame = from_robopianist_arrays(
            tick=0, timestamp_ns=100, goal=goal, activation=activation,
            sustain_activation=0.7,
            finger_contacts=[{"hand": "left", "finger": 1, "key": 3},
                             {"hand": "right", "finger": 2, "key": 8}],
        )
        self.assertEqual(frame["target_keys"], [3, 8])
        self.assertEqual(frame["pressed_keys"], [3, 8])
        self.assertTrue(frame["pressed_sustain"])
        result = score_trace({
            "schema": SCHEMA, "source": "robopianist-explicit-export",
            "upstream_ref": "a" * 40, "seed": 123, "frames": [frame]
        })
        self.assertEqual(result["matching_pressed_keys_with_reported_finger"], 2)
        self.assertEqual(result["key_micro_f1"], 1.0)
        self.assertEqual(result["exact_frame_rate"], 1.0)

    def test_bad_arrays_rejected(self):
        base_goal = [0] * (KEYS + 1)
        base_activation = [0.0] * KEYS
        def call(**kwargs):
            args = dict(tick=0, timestamp_ns=0, goal=base_goal.copy(),
                        activation=base_activation.copy(),
                        sustain_activation=0.0)
            args.update(kwargs)
            return from_robopianist_arrays(**args)
        bad = [
            dict(tick=True), dict(timestamp_ns=-1),
            dict(goal=base_goal[:-1]), dict(goal=base_goal + [0]),
            dict(goal=[2] + base_goal[1:]),
            dict(goal=[float("nan")] + base_goal[1:]),
            dict(goal=[0] * ((KEYS + 1) * 34)),
            dict(activation=base_activation + [0]),
            dict(activation=[float("inf")] + base_activation[1:]),
            dict(activation=[True] + base_activation[1:]),
            dict(sustain_activation=-0.1),
            dict(finger_contacts=[{"hand": "left", "finger": True, "key": 1}]),
            dict(finger_contacts=[{"hand": "left", "finger": 0, "key": 0},
                                  {"hand": "left", "finger": 0, "key": 1}]),
        ]
        for case in bad:
            with self.subTest(case=repr(case)[:80]):
                with self.assertRaises(PianoBenchmarkError):
                    call(**case)

    def test_mutated_frames_fail_closed(self):
        mutations = [
            lambda d: d["frames"].append(d["frames"][0]),
            lambda d: d["frames"][1].update(timestamp_ns=0),
            lambda d: d["frames"][1].update(tick=5),
            lambda d: d["frames"][0].update(target_keys=[4, 0]),
            lambda d: d["frames"][0].update(pressed_keys=[0, 0]),
            lambda d: d["frames"][0].update(target_keys=[KEYS]),
            lambda d: d["frames"][0].update(pressed_keys=[True]),
            lambda d: d["frames"][0].update(pressed_sustain=1),
            lambda d: d["frames"][0].update(secret="private"),
            lambda d: d.update(private_history="must not be copied"),
            lambda d: d.update(source="from-private-noosphere"),
            lambda d: d.update(seed=True),
            lambda d: d.update(upstream_ref="unverified"),
        ]
        for mutate in mutations:
            data = demo_trace()
            mutate(data)
            with self.subTest(mutate=repr(mutate)):
                with self.assertRaises(PianoBenchmarkError):
                    score_trace(data)

    def test_real_export_requires_full_lowercase_commit_hash(self):
        d = demo_trace()
        d["source"] = "robopianist-explicit-export"
        for ref in ("main", "a" * 39, "a" * 40 + "x", "A" * 40, "a" * 39 + "/"):
            d["upstream_ref"] = ref
            with self.subTest(ref=ref):
                with self.assertRaises(PianoBenchmarkError):
                    score_trace(d)
        d["upstream_ref"] = "d" * 40
        self.assertEqual(score_trace(d)["upstream_ref"], "d" * 40)

    def test_silence_is_not_fake_positive_f1(self):
        d = demo_trace()
        d["frames"] = [d["frames"][2]]
        self.assertEqual(score_trace(d)["key_micro_f1"], 0.0)
        self.assertEqual(score_trace(d)["silent_control_exact_frame_rate"], 1.0)

    def test_size_and_duplicate_field_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.json"
            path.write_text(json.dumps(demo_trace()), encoding="utf-8")
            self.assertEqual(read_trace(path), score_trace(demo_trace()))
            with self.assertRaisesRegex(PianoBenchmarkError, "input must be a local Path"):
                read_trace(str(path))
            for raw in ('{"schema":"x","schema":"y"}',
                        '{"oops":NaN}',
                        "[" * 1200 + "]" * 1200):
                with self.subTest(raw=raw[:30]):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaises(PianoBenchmarkError):
                        read_trace(path)
            path.write_bytes(b" " * (1_048_576 + 1))
            with self.assertRaisesRegex(PianoBenchmarkError, "oversized trace"):
                read_trace(path)

    def test_oversized_integer_literal_is_typed_trace_boundary_error(self):
        # CPython 3.11+ rejects this numeric token during json.loads before
        # score_trace can validate the seed. Never leak a raw ValueError.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "too-many-digits.json"
            raw = json.dumps(demo_trace()).replace('"seed": 700', '"seed": ' + "9" * 5000)
            path.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(PianoBenchmarkError, "invalid trace JSON"):
                read_trace(path)

    def test_missing_and_directory_input_fail_as_typed_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with self.assertRaisesRegex(PianoBenchmarkError, "cannot read local trace") as missing_err:
                read_trace(missing)
            self.assertIsNone(missing_err.exception.__cause__)
            with self.assertRaisesRegex(PianoBenchmarkError, "cannot read local trace") as dir_err:
                read_trace(Path(tmp))
            self.assertIsNone(dir_err.exception.__cause__)

    def test_contact_bounded_and_not_assumed_real(self):
        d = demo_trace()
        d["frames"][0]["finger_contacts"] = [
            {"hand": "left", "finger": 0, "key": 0},
            {"hand": "left", "finger": 1, "key": 4},
        ]
        score = score_trace(d)
        self.assertEqual(score["matching_pressed_keys_with_reported_finger"], 2)
        self.assertFalse(score["training_or_real_physics_verified"])
        d["frames"][0]["finger_contacts"].append(
            {"hand": "left", "finger": 0, "key": 1}
        )
        with self.assertRaises(PianoBenchmarkError):
            score_trace(d)

    def test_frame_cap(self):
        d = demo_trace()
        frame = copy.deepcopy(d["frames"][2])
        d["frames"] = [{**frame, "tick": i, "timestamp_ns": i * 1000}
                       for i in range(4097)]
        with self.assertRaises(PianoBenchmarkError):
            score_trace(d)


if __name__ == "__main__":
    unittest.main()
