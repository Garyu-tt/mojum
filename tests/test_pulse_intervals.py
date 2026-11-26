import hashlib
import json
import unittest

from mojum.pulse_intervals import (
    PulseIntervalError,
    analyze_pulse_intervals,
)


class PulseIntervalsTests(unittest.TestCase):
    def analyze(self, pulses, *, ticks=None, dt_ns=10_000_000):
        if ticks is None:
            ticks = list(range(len(pulses)))
        frames = [
            {"tick": tick, "pulse": pulse}
            for tick, pulse in zip(ticks, pulses)
        ]
        return analyze_pulse_intervals(
            frames, dt_ns=dt_ns, classification="PUBLIC_SYNTHETIC"
        )

    def test_repeated_regular_edges_and_exact_statistics(self):
        report = self.analyze([False, True, True, False, False, True, False, False, True])
        self.assertEqual(report["intervals_ns"], [40_000_000, 30_000_000])
        self.assertEqual(report["minimum_interval_ns"], 30_000_000)
        self.assertEqual(report["maximum_interval_ns"], 40_000_000)
        self.assertEqual(report["mean_interval_ns_floor"], 35_000_000)
        self.assertEqual(report["median_interval_ns_floor"], 35_000_000)
        self.assertEqual(report["status"], "OK")

    def test_single_edge_is_insufficient(self):
        report = self.analyze([False, True, True])
        self.assertEqual(report["rising_edge_count"], 1)
        self.assertEqual(report["interval_count"], 0)
        self.assertIsNone(report["median_interval_ns_floor"])

    def test_initial_high_is_not_assumed_to_be_an_edge(self):
        report = self.analyze([True, True, False, True, False, True])
        self.assertEqual(report["unanchored_high_segment_count"], 1)
        self.assertEqual(report["rising_edge_count"], 2)
        self.assertEqual(report["intervals_ns"], [20_000_000])

    def test_gap_splits_continuity_and_excludes_cross_gap_interval(self):
        report = self.analyze(
            [False, True, False, True, False, True],
            ticks=[0, 1, 2, 3, 8, 9],
        )
        self.assertEqual(report["missing_frame_count"], 4)
        self.assertEqual(report["rising_edge_count"], 3)
        self.assertEqual(report["intervals_ns"], [20_000_000])
        self.assertEqual(report["intervals_excluded_at_gaps"], 1)

    def test_digest_is_canonical_and_repeatable(self):
        frames = [{"tick": i, "pulse": value} for i, value in enumerate([False, True, False, True])]
        first = analyze_pulse_intervals(frames, dt_ns=10_000_000, classification="PUBLIC_SYNTHETIC")
        second = analyze_pulse_intervals(frames, dt_ns=10_000_000, classification="PUBLIC_SYNTHETIC")
        expected = hashlib.sha256(json.dumps(
            {k: v for k, v in first.items() if k != "sha256"},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()
        self.assertEqual(first, second)
        self.assertEqual(first["sha256"], expected)

    def test_rejects_invalid_classification_and_step(self):
        frames = [{"tick": 0, "pulse": False}]
        for classification in ("PRIVATE", "public_synthetic", None):
            with self.subTest(classification=classification):
                with self.assertRaises(PulseIntervalError):
                    analyze_pulse_intervals(frames, dt_ns=10_000_000, classification=classification)
        for dt in (True, 0, 999, 1_000_000_001):
            with self.subTest(dt=dt):
                with self.assertRaises(PulseIntervalError):
                    analyze_pulse_intervals(frames, dt_ns=dt, classification="PUBLIC_SYNTHETIC")

    def test_rejects_boolean_tick_and_non_boolean_pulse(self):
        cases = [
            [{"tick": True, "pulse": False}],
            [{"tick": 0, "pulse": 1}],
            [{"tick": 0, "pulse": False, "extra": 1}],
        ]
        for frames in cases:
            with self.subTest(frames=frames):
                with self.assertRaises(PulseIntervalError):
                    analyze_pulse_intervals(frames, dt_ns=10_000_000, classification="PUBLIC_SYNTHETIC")

    def test_rejects_string_subclass_keys_before_key_set_comparison(self):
        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise RuntimeError("hostile equality must not run")

        frames = [{HostileKey("tick"): 0, "pulse": False}]
        with self.assertRaises(PulseIntervalError):
            analyze_pulse_intervals(frames, dt_ns=10_000_000, classification="PUBLIC_SYNTHETIC")

    def test_rejects_duplicate_or_decreasing_ticks(self):
        with self.assertRaises(PulseIntervalError):
            self.analyze([False, True, False], ticks=[0, 1, 1])
        with self.assertRaises(PulseIntervalError):
            self.analyze([False, True, False], ticks=[2, 1, 3])

    def test_rejects_empty_oversized_and_wrong_container(self):
        for frames in ([], {}, [{"tick": i, "pulse": False} for i in range(10_001)]):
            with self.subTest(length=(len(frames) if hasattr(frames, "__len__") else None)):
                with self.assertRaises(PulseIntervalError):
                    analyze_pulse_intervals(frames, dt_ns=10_000_000, classification="PUBLIC_SYNTHETIC")

    def test_rejects_tick_timestamp_overflow(self):
        with self.assertRaises(PulseIntervalError):
            analyze_pulse_intervals(
                [{"tick": 2**63, "pulse": False}],
                dt_ns=1_000_000_000,
                classification="PUBLIC_SYNTHETIC",
            )


if __name__ == "__main__":
    unittest.main()
