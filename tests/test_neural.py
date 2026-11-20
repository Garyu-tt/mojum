import copy
import math
import unittest

from mojum.neural import NeuralError, PulsePredictor, SCHEMA


def sample(tick, *, phase=None, pulse=None, dt_ns=10_000_000):
    p = (tick * 10) % 1000 if phase is None else phase
    return {
        "kind": "interoception",
        "observed_at_ns": tick * dt_ns,
        "heartbeat": {
            "phase_milli": p,
            "pulse": (p < 125 if type(p) is int else False) if pulse is None else pulse,
            "synthetic": True,
        },
    }


class PulseLearningTests(unittest.TestCase):
    def test_first_sample_is_not_leaked_as_label(self):
        p = PulsePredictor()
        frame = p.observe(sample(0))
        self.assertFalse(frame["trained"])
        self.assertIsNone(frame["prediction_from_prior_tick"])
        self.assertEqual(p.updates, 0)

    def test_second_sample_has_one_delayed_update(self):
        p = PulsePredictor()
        p.observe(sample(0))
        r = p.observe(sample(1))
        self.assertTrue(r["trained"])
        self.assertEqual(r["prediction_from_prior_tick"], 0.5)
        self.assertEqual(r["updates"], 1)

    def test_weights_are_actually_updated(self):
        p = PulsePredictor()
        before = p.snapshot()["weights"]
        p.observe(sample(0))
        p.observe(sample(1))
        self.assertNotEqual(before, p.snapshot()["weights"])

    def test_zero_pulse_stream_loss_decreases(self):
        p = PulsePredictor()
        losses = []
        for tick in range(110):
            frame = p.observe(sample(tick, phase=500, pulse=False))
            if frame["squared_error"] is not None:
                losses.append(frame["squared_error"])
        self.assertLess(sum(losses[-15:]) / 15, sum(losses[:15]) / 15)
        self.assertLess(p.snapshot()["loss_sum"] / p.updates, 0.25)

    def test_snapshot_replay_identical(self):
        p = PulsePredictor()
        for tick in range(27):
            p.observe(sample(tick))
        checkpoint = p.snapshot()
        original = [p.observe(sample(tick)) for tick in range(27, 75)]
        restored = PulsePredictor.from_snapshot(checkpoint)
        replay = [restored.observe(sample(tick)) for tick in range(27, 75)]
        self.assertEqual(original, replay)
        self.assertEqual(p.snapshot(), restored.snapshot())

    def test_identical_experiences_produce_identical_state(self):
        p, q = PulsePredictor(), PulsePredictor()
        for tick in range(120):
            self.assertEqual(p.observe(sample(tick)), q.observe(sample(tick)))
        self.assertEqual(p.snapshot(), q.snapshot())

    def test_checkpoint_copies_state(self):
        p = PulsePredictor()
        p.observe(sample(0))
        snapshot = p.snapshot()
        snapshot["weights"][0] = 999
        snapshot["pending_features"][0] = -1
        self.assertNotEqual(p.snapshot()["weights"][0], 999)
        self.assertEqual(p.snapshot()["pending_features"][0], 1.0)

    def test_reject_stale_sensory_timestamps(self):
        p = PulsePredictor()
        p.observe(sample(0))
        for bad in (0, -1):
            with self.assertRaises(NeuralError):
                p.observe(sample(bad))
        self.assertEqual(p.updates, 0)

    def test_reject_boolean_timestamp(self):
        p = PulsePredictor()
        x = sample(0)
        x["observed_at_ns"] = True
        with self.assertRaises(NeuralError):
            p.observe(x)

    def test_reject_bad_pulse_shapes(self):
        for pulse in (None, 1, "true", [], float("nan")):
            with self.subTest(pulse=pulse):
                p = PulsePredictor()
                x = sample(0)
                x["heartbeat"]["pulse"] = pulse
                with self.assertRaises(NeuralError):
                    p.observe(x)

    def test_reject_bad_phases(self):
        for phase in (True, -1, 1000, 0.5, "55"):
            with self.subTest(phase=phase):
                p = PulsePredictor()
                with self.assertRaises(NeuralError):
                    p.observe(sample(0, phase=phase))

    def test_reject_bad_learning_rate(self):
        for rate in (False, 0, -0.1, 1, math.inf, math.nan, "0.1"):
            with self.subTest(rate=rate), self.assertRaises(NeuralError):
                PulsePredictor(rate)

    def test_snapshot_rejects_nan_weight(self):
        p = PulsePredictor()
        p.observe(sample(0))
        s = p.snapshot()
        s["weights"][1] = math.nan
        with self.assertRaises(NeuralError):
            PulsePredictor.from_snapshot(s)

    def test_snapshot_rejects_bad_counter(self):
        s = PulsePredictor().snapshot()
        s["updates"] = True
        with self.assertRaises(NeuralError):
            PulsePredictor.from_snapshot(s)

    def test_snapshot_rejects_unbounded_weight(self):
        s = PulsePredictor().snapshot()
        s["weights"][0] = 200
        with self.assertRaises(NeuralError):
            PulsePredictor.from_snapshot(s)

    def test_snapshot_rejects_unknown_schema(self):
        s = PulsePredictor().snapshot()
        s["schema"] = "mojum.neural.hidden/2"
        with self.assertRaises(NeuralError):
            PulsePredictor.from_snapshot(s)

    def test_weights_remain_bounded(self):
        p = PulsePredictor(0.5)
        for tick in range(2500):
            p.observe(sample(tick, pulse=tick % 2 == 0))
        self.assertTrue(all(-2 <= w <= 2 for w in p.snapshot()["weights"]))

    def test_no_motor_or_planning_method(self):
        p = PulsePredictor()
        for name in ("actuate", "plan", "send_motor", "think", "train_llm"):
            self.assertFalse(hasattr(p, name))
        self.assertIsNone(p.observe(sample(0))["motor_intention"])

    def test_exposes_clear_training_status(self):
        p = PulsePredictor()
        row = p.observe(sample(0))
        self.assertEqual(row["schema"], SCHEMA)
        self.assertEqual(row["status"], "ONLINE_SHADOW_PREDICTOR")
        self.assertEqual(row["training_source"], "synthetic_heartbeat_delayed_self_supervision")


if __name__ == "__main__":
    unittest.main()
