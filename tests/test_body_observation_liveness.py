import copy
import unittest

from mojum.body_observation_liveness import (
    OBSERVATION_SCHEMA,
    ObservationLivenessError,
    audit_observation_liveness,
)


def frame(sequence, sample_time_ns=None, **overrides):
    value = {
        "schema": OBSERVATION_SCHEMA,
        "visibility": "PUBLIC_SYNTHETIC",
        "consent": "explicit",
        "stream_id": "fixture-body",
        "source_id": "synthetic-proprioception",
        "sequence": sequence,
        "sample_time_ns": sequence * 10 if sample_time_ns is None else sample_time_ns,
    }
    value.update(overrides)
    return value


def audit(frames, **kwargs):
    limits = {
        "evaluated_at_ns": 100,
        "max_age_ns": 30,
        "max_sample_gap_ns": 20,
    }
    limits.update(kwargs)
    return audit_observation_liveness(frames, **limits)


class ObservationLivenessTests(unittest.TestCase):
    def test_contiguous_fresh_stream_is_live_and_deterministic(self):
        frames = [frame(1), frame(2), frame(3)]
        first = audit(frames)
        second = audit(copy.deepcopy(frames))
        self.assertTrue(first.is_live)
        self.assertTrue(first.is_contiguous)
        self.assertTrue(first.is_fresh)
        self.assertEqual(first.max_inter_sample_gap_ns, 10)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.sequence_gap_ranges, ())

    def test_sequence_gaps_are_bounded_ranges_not_expanded(self):
        report = audit([frame(2), frame(5), frame(9)])
        self.assertFalse(report.is_live)
        self.assertEqual(report.missing_sequence_count, 5)
        self.assertEqual(report.sequence_gap_ranges, ((3, 4), (6, 8)))
        self.assertIn("sequence_gap", report.reasons)

    def test_sample_interval_budget_and_freshness_are_independent(self):
        gap = audit([frame(1, 10), frame(2, 50)], evaluated_at_ns=55)
        self.assertFalse(gap.within_sample_gap_budget)
        self.assertTrue(gap.is_fresh)
        self.assertIn("sample_gap_exceeded", gap.reasons)

        stale = audit([frame(1, 10)], evaluated_at_ns=50, max_age_ns=20)
        self.assertFalse(stale.is_fresh)
        self.assertIn("stale", stale.reasons)

    def test_future_sample_is_not_fresh(self):
        report = audit([frame(1, 101)], evaluated_at_ns=100)
        self.assertFalse(report.is_fresh)
        self.assertIn("future_sample", report.reasons)

    def test_requires_one_stream_and_strictly_increasing_clocks(self):
        with self.assertRaises(ObservationLivenessError):
            audit([frame(1), frame(2, 20, source_id="other")])
        with self.assertRaises(ObservationLivenessError):
            audit([frame(1), frame(1, 20)])
        with self.assertRaises(ObservationLivenessError):
            audit([frame(1, 20), frame(2, 20)])

    def test_explicit_public_synthetic_consent_is_mandatory(self):
        for key, value in (("visibility", "PRIVATE"), ("consent", "implicit")):
            bad = frame(1)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ObservationLivenessError):
                audit([bad])
        bad = frame(1)
        bad["schema"] = "future/2"
        with self.assertRaises(ObservationLivenessError):
            audit([bad])

    def test_native_types_only_bool_is_not_integer(self):
        for key, value in (("sequence", True), ("sample_time_ns", 1.5),
                           ("sample_time_ns", float("nan"))):
            bad = frame(1)
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ObservationLivenessError):
                audit([bad])
        with self.assertRaises(ObservationLivenessError):
            audit([frame(1)], evaluated_at_ns=True)

    def test_bounds_and_exact_shape(self):
        for bad in (None, (), [frame(1)] * 4097):
            with self.assertRaises(ObservationLivenessError):
                audit(bad)
        missing = frame(1)
        del missing["consent"]
        with self.assertRaises(ObservationLivenessError):
            audit([missing])
        extra = frame(1)
        extra["payload"] = {"arbitrary": "data"}
        with self.assertRaises(ObservationLivenessError):
            audit([extra])

    def test_hostile_container_subclasses_fail_closed(self):
        class EvilDict(dict):
            pass

        class EvilList(list):
            pass

        with self.assertRaises(ObservationLivenessError):
            audit(EvilList([frame(1)]))
        with self.assertRaises(ObservationLivenessError):
            audit([EvilDict(frame(1))])

    def test_report_is_immutable_and_does_not_retain_payloads(self):
        supplied = [frame(1), frame(2)]
        report = audit(supplied)
        supplied[0]["stream_id"] = "mutated"
        self.assertEqual(report.stream_id, "fixture-body")
        with self.assertRaises((AttributeError, TypeError)):
            report.is_live = False
        self.assertFalse(hasattr(report, "frames"))

    def test_large_identifier_and_canonical_byte_budget(self):
        with self.assertRaises(ObservationLivenessError):
            audit([frame(1, stream_id="x" * 97)])
        huge = frame(1, source_id="x" * 96)
        # Repeated bounded frames can still exceed the total canonical byte cap.
        with self.assertRaises(ObservationLivenessError):
            audit([dict(huge, sequence=i, sample_time_ns=i) for i in range(4096)])


if __name__ == "__main__":
    unittest.main()
