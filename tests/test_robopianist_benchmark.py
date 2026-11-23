"""Hermetic tests for the synthetic RoboPianist-style benchmark contract."""
import copy
import math
import unittest
from unittest.mock import patch

from mojum.robopianist_benchmark import (
    BenchmarkError, TASK_SCHEMA, compare_controls, make_task, make_trace, score_trace, validate_task, _digest,
)


class HostileKey(str):
    def __hash__(self):
        return hash(str(self))
    def __eq__(self, other):
        raise AssertionError("hostile key comparison executed")


class HostileText(str):
    def __eq__(self, other):
        raise AssertionError("hostile scalar comparison executed")


class RoboPianistBenchmarkTests(unittest.TestCase):
    def test_repeatable_policy_and_frozen_control(self):
        task = make_task(42)
        self.assertEqual(task, make_task(42))
        comparison = compare_controls(task)
        self.assertEqual(comparison["policy"]["pitch_accuracy"], 1.0)
        self.assertGreater(comparison["policy"]["pitch_accuracy"],
                           comparison["frozen_control"]["pitch_accuracy"])
        self.assertFalse(comparison["learning_claimed"])
        self.assertFalse(comparison["transfer_measured"])

    def test_held_out_split_and_seed_change_are_deterministic(self):
        a = make_task(7, held_out=True)
        b = make_task(7, held_out=True)
        self.assertEqual(a, b)
        self.assertEqual(a["split"], "held_out")
        self.assertNotEqual(a["sha256"], make_task(8, held_out=True)["sha256"])
        self.assertNotEqual(a["sha256"], make_task(7)["sha256"])

    def test_score_requires_explicit_consent_and_matching_task(self):
        task = make_task(1)
        trace = make_trace(task)
        with self.assertRaises(BenchmarkError):
            score_trace(task, trace)
        with self.assertRaises(BenchmarkError):
            score_trace(make_task(2), trace, consent=True)
        self.assertEqual(score_trace(task, trace, consent=True)["correct_notes"],
                         len(task["notes"]))

    def test_rejects_bool_types_bounds_and_bad_seed(self):
        for seed in (True, -1, 2**32):
            with self.assertRaises(BenchmarkError):
                make_task(seed)
        for count in (True, 3, 65):
            with self.assertRaises(BenchmarkError):
                make_task(1, note_count=count)
        task = make_task(2)
        bad = copy.deepcopy(task)
        bad["notes"][0]["key"] = True
        with self.assertRaises(BenchmarkError):
            validate_task(bad)
        bad = copy.deepcopy(task)
        bad["consent"] = False
        with self.assertRaises(BenchmarkError):
            validate_task(bad)

    def test_rejects_tampered_trace_and_nonfinite_values(self):
        task = make_task(3)
        trace = make_trace(task)
        bad = copy.deepcopy(trace)
        bad["events"][0]["tick"] = math.inf
        with self.assertRaises(BenchmarkError):
            score_trace(task, bad, consent=True)
        bad = copy.deepcopy(trace)
        bad["events"][0]["key"] = 60
        with self.assertRaises(BenchmarkError):
            score_trace(task, bad, consent=True)

    def test_input_mutation_does_not_change_detached_validation(self):
        task = make_task(4)
        checked = validate_task(task)
        task["notes"][0]["key"] = 59
        self.assertNotEqual(checked["notes"][0][1], task["notes"][0]["key"])

    def test_invalid_mode_and_trace_order(self):
        task = make_task(5)
        with self.assertRaises(BenchmarkError):
            make_trace(task, mode="train-the-organism")
        trace = make_trace(task)
        bad = copy.deepcopy(trace)
        bad["events"][0], bad["events"][1] = bad["events"][1], bad["events"][0]
        # Digest mismatch is sufficient to reject any altered event ordering.
        with self.assertRaises(BenchmarkError):
            score_trace(task, bad, consent=True)

    def test_rejects_hostile_keys_before_hash_or_equality_comparison(self):
        task = make_task(17)
        bad = dict(task)
        bad[HostileKey("extra")] = None
        with self.assertRaises(BenchmarkError):
            validate_task(bad)

        bad = copy.deepcopy(task)
        note = bad["notes"][0]
        note[HostileKey("extra")] = None
        with self.assertRaises(BenchmarkError):
            validate_task(bad)

        trace = make_trace(task)
        bad_trace = dict(trace)
        bad_trace[HostileKey("extra")] = None
        with self.assertRaises(BenchmarkError):
            score_trace(task, bad_trace, consent=True)

        bad_trace = copy.deepcopy(trace)
        bad_trace["events"][0][HostileKey("extra")] = None
        with self.assertRaises(BenchmarkError):
            score_trace(task, bad_trace, consent=True)

    def test_make_trace_rejects_hostile_mode_subclass(self):
        task = make_task(19)
        with self.assertRaises(BenchmarkError):
            make_trace(task, mode=HostileText("policy"))

    def test_rejects_hostile_text_subclasses_before_scalar_comparison(self):
        task = make_task(18)
        bad = dict(task)
        bad["schema"] = HostileText(TASK_SCHEMA)
        with self.assertRaises(BenchmarkError):
            validate_task(bad)

        trace = make_trace(task)
        bad_trace = dict(trace)
        bad_trace["mode"] = HostileText("policy")
        with self.assertRaises(BenchmarkError):
            score_trace(task, bad_trace, consent=True)

    def test_rejects_nonchronological_trace_with_valid_digest(self):
        task = make_task(21)
        trace = make_trace(task)
        bad = copy.deepcopy(trace)
        bad["events"][1]["tick"] = bad["events"][0]["tick"]
        unsigned = {k: bad[k] for k in ("schema", "provenance", "consent", "task_sha256", "mode", "events")}
        bad["sha256"] = _digest(unsigned)
        with self.assertRaisesRegex(BenchmarkError, "strictly increasing"):
            score_trace(task, bad, consent=True)

    def test_duration_accuracy_is_reported(self):
        task = make_task(22)
        trace = make_trace(task)
        trace["events"][0]["duration_ticks"] += 1
        unsigned = {k: trace[k] for k in ("schema", "provenance", "consent", "task_sha256", "mode", "events")}
        trace["sha256"] = _digest(unsigned)
        report = score_trace(task, trace, consent=True)
        self.assertEqual(report["correct_durations"], len(task["notes"]) - 1)
        self.assertLess(report["duration_accuracy"], 1.0)

    def test_rejects_oversized_envelope_text_before_comparison_or_digest(self):
        task = make_task(31)
        for field in ("schema", "provenance", "split", "sha256"):
            bad = copy.deepcopy(task)
            bad[field] = "x" * 1_000_000
            with self.subTest(envelope="task", field=field), self.assertRaisesRegex(BenchmarkError, "length budget"):
                validate_task(bad)
        trace = make_trace(task)
        for field in ("schema", "provenance", "task_sha256", "mode", "sha256"):
            bad = copy.deepcopy(trace)
            bad[field] = "x" * 1_000_000
            with self.subTest(envelope="trace", field=field), self.assertRaisesRegex(BenchmarkError, "length budget"):
                score_trace(task, bad, consent=True)

    def test_no_network_or_device_side_effects(self):
        task = make_task(6)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = compare_controls(task)
        self.assertFalse(result["learning_claimed"])


if __name__ == "__main__":
    unittest.main()
