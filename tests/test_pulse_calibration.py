import math
import unittest
from dataclasses import FrozenInstanceError

from mojum.pulse_calibration import (
    CalibrationError,
    MAX_SAMPLES,
    MAX_TICK,
    SCHEMA,
    audit_calibration as _audit_calibration,
)


def row(tick, forecast, observed):
    return {"tick": tick, "forecast": forecast, "observed": observed}


def audit_calibration(observations, *, bin_count=10):
    return _audit_calibration(
        observations,
        source_classification="PUBLIC_SYNTHETIC",
        bin_count=bin_count,
    )


class HostileString(str):
    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise RuntimeError("hostile equality must not run")


class PulseCalibrationTests(unittest.TestCase):
    def test_brier_score_and_bins_are_correct(self):
        report = audit_calibration([
            row(1, 0.1, False),
            row(2, 0.19, False),
            row(3, 0.8, True),
            row(4, 1.0, True),
        ], bin_count=5)
        self.assertEqual(report.schema, SCHEMA)
        self.assertEqual(report.provenance, "PUBLIC_SYNTHETIC")
        self.assertEqual(report.sample_count, 4)
        self.assertAlmostEqual(report.brier_score, (0.01 + 0.0361 + 0.04 + 0.0) / 4)
        self.assertEqual(len(report.bins), 5)
        self.assertEqual(report.bins[0].sample_count, 2)
        self.assertAlmostEqual(report.bins[0].mean_forecast, 0.145)
        self.assertEqual(report.bins[0].observed_frequency, 0.0)
        self.assertAlmostEqual(report.bins[0].absolute_gap, 0.145)
        self.assertEqual(report.bins[1].sample_count, 0)
        self.assertIsNone(report.bins[1].mean_forecast)
        self.assertEqual(report.bins[3].sample_count, 1)
        self.assertEqual(report.bins[4].sample_count, 1)
        self.assertEqual(report.bins[4].observed_frequency, 1.0)

    def test_internal_bin_boundaries_are_right_closed(self):
        report = audit_calibration([
            row(0, 0.2, False),
            row(1, 0.200001, False),
            row(2, 0.8, True),
            row(3, 0.800001, True),
        ], bin_count=5)
        self.assertEqual(
            [item.sample_count for item in report.bins],
            [1, 1, 0, 1, 1],
        )

    def test_exact_upper_probability_enters_last_bin(self):
        report = audit_calibration([row(0, 1, True)], bin_count=10)
        self.assertEqual(report.bins[-1].sample_count, 1)

    def test_all_represented_internal_boundaries_stay_in_lower_bin(self):
        bin_count = 25
        observations = [
            row(tick, boundary, False)
            for tick, boundary in enumerate(index / bin_count for index in range(1, bin_count))
        ]
        report = audit_calibration(observations, bin_count=bin_count)
        self.assertEqual(
            [item.sample_count for item in report.bins],
            [1] * (bin_count - 1) + [0],
        )

    def test_deterministic_digest_for_same_values(self):
        values = [row(1, 0.25, False), row(4, 0.75, True)]
        first = audit_calibration(values, bin_count=4)
        second = audit_calibration(values, bin_count=4)
        self.assertEqual(first, second)
        self.assertEqual(len(first.report_sha256), 64)

    def test_digest_is_independent_of_input_dict_insertion_order(self):
        a = {"tick": 1, "forecast": 0.5, "observed": True}
        b = {"observed": True, "forecast": 0.5, "tick": 1}
        self.assertEqual(
            audit_calibration([a]).report_sha256,
            audit_calibration([b]).report_sha256,
        )

    def test_result_is_detached_and_immutable(self):
        values = [row(1, 0.5, True)]
        report = audit_calibration(values)
        digest = report.report_sha256
        values[0]["forecast"] = 0.0
        self.assertEqual(report.report_sha256, digest)
        with self.assertRaises(FrozenInstanceError):
            report.sample_count = 10
        with self.assertRaises(FrozenInstanceError):
            report.bins[0].sample_count = 10
        with self.assertRaises(TypeError):
            report.bins[0] = report.bins[0]

    def test_rejects_empty_or_oversized_observations(self):
        for values in ([], [row(i, 0.5, False) for i in range(MAX_SAMPLES + 1)]):
            with self.subTest(length=len(values)), self.assertRaises(CalibrationError):
                audit_calibration(values)

    def test_requires_explicit_public_synthetic_classification(self):
        sample = [row(1, 0.5, True)]
        for classification in (None, "PRIVATE", "PUBLIC", "public_synthetic", 1):
            with self.subTest(classification=classification), self.assertRaises(CalibrationError):
                _audit_calibration(sample, source_classification=classification)
        with self.assertRaises(CalibrationError):
            _audit_calibration(sample)

    def test_rejects_container_subclasses(self):
        class Rows(list):
            pass

        class Row(dict):
            pass

        with self.assertRaises(CalibrationError):
            audit_calibration(Rows([row(1, 0.5, True)]))
        with self.assertRaises(CalibrationError):
            audit_calibration([Row(row(1, 0.5, True))])

    def test_rejects_hostile_non_native_keys_before_comparison(self):
        value = {"forecast": 0.5, "observed": True}
        value[HostileString("tick")] = 1
        with self.assertRaises(CalibrationError):
            audit_calibration([value])

    def test_rejects_extra_missing_and_non_string_fields(self):
        bad = [
            {"tick": 1, "forecast": 0.5},
            {"tick": 1, "forecast": 0.5, "observed": True, "extra": 1},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(CalibrationError):
                audit_calibration([value])

    def test_rejects_bool_tick_and_non_monotonic_ticks(self):
        for values in (
            [row(True, 0.5, True)],
            [row(2, 0.5, True), row(2, 0.5, False)],
            [row(3, 0.5, True), row(2, 0.5, False)],
        ):
            with self.subTest(values=values), self.assertRaises(CalibrationError):
                audit_calibration(values)

    def test_rejects_invalid_forecasts(self):
        for forecast in (True, False, -0.01, 1.01, math.nan, math.inf, -math.inf, "0.5", None):
            with self.subTest(forecast=forecast), self.assertRaises(CalibrationError):
                audit_calibration([row(1, forecast, True)])

    def test_rejects_non_boolean_labels(self):
        for observed in (0, 1, "true", None, [], float("nan")):
            with self.subTest(observed=observed), self.assertRaises(CalibrationError):
                audit_calibration([row(1, 0.5, observed)])

    def test_tick_bounds_are_explicit(self):
        self.assertEqual(audit_calibration([row(MAX_TICK, 0.5, True)]).sample_count, 1)
        with self.assertRaises(CalibrationError):
            audit_calibration([row(MAX_TICK + 1, 0.5, True)])

    def test_rejects_invalid_bin_count(self):
        for count in (True, False, 0, -1, 101, 1.0, "10", None):
            with self.subTest(count=count), self.assertRaises(CalibrationError):
                audit_calibration([row(1, 0.5, True)], bin_count=count)

    def test_input_is_never_modified(self):
        values = [row(1, 0.25, False), row(2, 0.75, True)]
        before = [dict(item) for item in values]
        audit_calibration(values)
        self.assertEqual(values, before)

    def test_empty_bins_are_explicit_for_single_observation(self):
        report = audit_calibration([row(0, 0.99, True)], bin_count=3)
        self.assertEqual([item.sample_count for item in report.bins], [0, 0, 1])
        self.assertIsNone(report.bins[0].absolute_gap)

    def test_report_never_echoes_arbitrary_input_payload(self):
        report = audit_calibration([row(123, 0.5, True)])
        self.assertFalse(hasattr(report, "source_payload"))
        self.assertFalse(hasattr(report, "event_ids"))

    def test_probability_one_and_zero_are_valid_exact_values(self):
        report = audit_calibration([row(0, 0, False), row(1, 1, True)])
        self.assertEqual(report.brier_score, 0.0)


if __name__ == "__main__":
    unittest.main()
