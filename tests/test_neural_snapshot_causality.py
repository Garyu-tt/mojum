"""Cold neural restore must reject snapshots impossible under observe()."""

import copy
import unittest

from mojum.neural import NeuralError, PulsePredictor


def sample(at_ns, *, pulse=False):
    return {
        "kind": "interoception",
        "observed_at_ns": at_ns,
        "heartbeat": {"phase_milli": 500, "pulse": pulse, "synthetic": True},
    }


class NeuralSnapshotCausalityTests(unittest.TestCase):
    def test_first_observation_cannot_claim_a_training_update(self):
        predictor = PulsePredictor()
        predictor.observe(sample(0))
        original = predictor.snapshot()
        forged = copy.deepcopy(original)
        forged["updates"] = 1
        with self.assertRaisesRegex(NeuralError, "sensory time"):
            PulsePredictor.from_snapshot(forged)
        self.assertEqual(predictor.snapshot(), original)

    def test_observed_timestamp_bounds_number_of_updates(self):
        predictor = PulsePredictor()
        for at_ns in (0, 1, 2):
            predictor.observe(sample(at_ns))
        original = predictor.snapshot()
        self.assertEqual(original["updates"], 2)
        self.assertEqual(original["last_observed_at_ns"], 2)
        self.assertEqual(PulsePredictor.from_snapshot(original).snapshot(), original)
        for impossible in (3, 100, 10**100):
            with self.subTest(updates=impossible):
                forged = copy.deepcopy(original)
                forged["updates"] = impossible
                with self.assertRaisesRegex(NeuralError, "sensory time"):
                    PulsePredictor.from_snapshot(forged)
        self.assertEqual(predictor.snapshot(), original)

    def test_loss_cannot_exceed_number_of_training_updates(self):
        predictor = PulsePredictor()
        predictor.observe(sample(0))
        predictor.observe(sample(1, pulse=True))
        original = predictor.snapshot()
        self.assertEqual(original["updates"], 1)
        for impossible_loss in (1.000001, 100.0):
            with self.subTest(loss=impossible_loss):
                forged = copy.deepcopy(original)
                forged["loss_sum"] = impossible_loss
                with self.assertRaisesRegex(NeuralError, "online-learning counters"):
                    PulsePredictor.from_snapshot(forged)
        self.assertEqual(predictor.snapshot(), original)

    def test_untrained_state_cannot_claim_nonzero_loss(self):
        original = PulsePredictor().snapshot()
        forged = copy.deepcopy(original)
        forged["loss_sum"] = 0.01
        with self.assertRaisesRegex(NeuralError, "online-learning counters"):
            PulsePredictor.from_snapshot(forged)
        self.assertEqual(PulsePredictor.from_snapshot(original).snapshot(), original)

    def test_sparse_real_timestamps_remain_backward_compatible(self):
        predictor = PulsePredictor()
        for at_ns, pulse in ((0, False), (1_000_000, True), (9_000_000, False)):
            predictor.observe(sample(at_ns, pulse=pulse))
        saved = predictor.snapshot()
        replay = PulsePredictor.from_snapshot(saved)
        self.assertEqual(replay.snapshot(), saved)
        self.assertEqual(
            replay.observe(sample(10_000_000, pulse=True)),
            predictor.observe(sample(10_000_000, pulse=True)),
        )


if __name__ == "__main__":
    unittest.main()
