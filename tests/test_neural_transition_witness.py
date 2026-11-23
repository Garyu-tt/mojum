import copy
import unittest

from mojum.neural import PulsePredictor
from mojum.neural_transition_witness import (
    SCHEMA,
    TransitionWitnessError,
    attest_transition,
)


def frame(tick, *, pulse=False, phase=500):
    return {
        "kind": "interoception",
        "observed_at_ns": tick * 10_000_000,
        "heartbeat": {"phase_milli": phase, "pulse": pulse, "synthetic": True},
    }


def replay(before, sample, summation="ordered"):
    predictor = PulsePredictor.from_snapshot(before, summation=summation)
    predictor.observe(sample)
    return predictor.snapshot()


class NeuralTransitionWitnessTests(unittest.TestCase):
    def attest(self, before, sample, after, **kwargs):
        return attest_transition(
            before, sample, after,
            classification="PUBLIC_SYNTHETIC", summation="ordered", **kwargs
        )

    def test_first_observation_matches_without_training(self):
        before = PulsePredictor().snapshot()
        sample = frame(0)
        after = replay(before, sample)
        report = self.attest(before, sample, after)
        self.assertEqual(report["schema"], SCHEMA)
        self.assertEqual(report["status"], "MATCH")
        self.assertFalse(report["trained"])
        self.assertEqual(report["updates_after"], 0)
        self.assertEqual(report["mismatch_fields"], [])

    def test_delayed_observation_matches_one_update(self):
        predictor = PulsePredictor()
        predictor.observe(frame(0, pulse=True, phase=100))
        before = predictor.snapshot()
        sample = frame(1, pulse=False, phase=700)
        after = replay(before, sample)
        report = self.attest(before, sample, after)
        self.assertEqual(report["status"], "MATCH")
        self.assertTrue(report["trained"])
        self.assertEqual(report["updates_after"], 1)

    def test_valid_but_forged_post_state_reports_mismatch(self):
        before = PulsePredictor().snapshot()
        sample = frame(0)
        after = replay(before, sample)
        after["weights"][0] = 0.6
        report = self.attest(before, sample, after)
        self.assertEqual(report["status"], "MISMATCH")
        self.assertEqual(report["mismatch_fields"], ["weights"])

    def test_compensated_summation_is_explicit_and_reproducible(self):
        before = PulsePredictor(summation="compensated").snapshot()
        sample = frame(0)
        after = replay(before, sample, summation="compensated")
        report = attest_transition(
            before, sample, after, classification="PUBLIC_SYNTHETIC",
            summation="compensated",
        )
        self.assertEqual(report["status"], "MATCH")
        self.assertEqual(report["summation"], "compensated")

    def test_requires_explicit_public_synthetic_classification(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        with self.assertRaises(TransitionWitnessError):
            attest_transition(state, sample, replay(state, sample),
                              classification="PRIVATE", summation="ordered")

    def test_rejects_unsupported_summation(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        with self.assertRaises(TransitionWitnessError):
            attest_transition(state, sample, replay(state, sample),
                              classification="PUBLIC_SYNTHETIC", summation="native")

    def test_rejects_missing_summation(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        with self.assertRaises(TypeError):
            attest_transition(state, sample, replay(state, sample),
                              classification="PUBLIC_SYNTHETIC")

    def test_rejects_extra_frame_fields(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        sample["future"] = True
        with self.assertRaises(TransitionWitnessError):
            self.attest(state, sample, replay(state, frame(0)))

    def test_rejects_non_synthetic_frame(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        sample["heartbeat"]["synthetic"] = False
        with self.assertRaises(TransitionWitnessError):
            self.attest(state, sample, replay(state, frame(0)))

    def test_rejects_hostile_container_subclasses(self):
        class SneakyDict(dict):
            pass
        state = PulsePredictor().snapshot()
        with self.assertRaises(TransitionWitnessError):
            self.attest(SneakyDict(state), frame(0), replay(state, frame(0)))

    def test_rejects_bool_timestamp(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        sample["observed_at_ns"] = True
        with self.assertRaises(TransitionWitnessError):
            self.attest(state, sample, replay(state, frame(0)))

    def test_stale_time_is_fail_closed(self):
        predictor = PulsePredictor()
        predictor.observe(frame(1))
        before = predictor.snapshot()
        sample = frame(1)
        with self.assertRaises(TransitionWitnessError):
            self.attest(before, sample, before)

    def test_inputs_are_not_mutated(self):
        before = PulsePredictor().snapshot()
        sample = frame(0)
        after = replay(before, sample)
        original = copy.deepcopy((before, sample, after))
        self.attest(before, sample, after)
        self.assertEqual((before, sample, after), original)

    def test_report_is_deterministic_and_redacted(self):
        before = PulsePredictor().snapshot()
        sample = frame(0)
        after = replay(before, sample)
        first = self.attest(before, sample, after)
        second = self.attest(before, sample, after)
        self.assertEqual(first, second)
        self.assertNotIn("weights", first)
        self.assertNotIn("heartbeat", first)
        self.assertEqual(len(first["witness_sha256"]), 64)

    def test_rejects_over_budget_input(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        with self.assertRaises(TransitionWitnessError):
            self.attest(state, sample, replay(state, sample), max_bytes=128)

    def test_rejects_invalid_post_snapshot(self):
        state = PulsePredictor().snapshot()
        sample = frame(0)
        after = replay(state, sample)
        after["updates"] = True
        with self.assertRaises(TransitionWitnessError):
            self.attest(state, sample, after)


if __name__ == "__main__":
    unittest.main()
