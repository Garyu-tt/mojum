"""Deterministic stdlib STDP safety tests and optional real Brian2 proof."""
import importlib.util
import math
import unittest
from mojum.brian2_stdp import (
    BackendUnavailable, STDPInputError, brian2_trace, compare_native,
    parse_case, public_fixture, reference_trace,
)


class STDPInputTests(unittest.TestCase):
    def setUp(self):
        self.document = public_fixture()

    def test_fixture_replay_deterministic(self):
        case = parse_case(self.document)
        self.assertEqual(reference_trace(case), reference_trace(case))
        self.assertEqual(len(reference_trace(case)), 8)

    def test_no_learning_on_single_pre_spike(self):
        self.document["events"] = [{"tick": 1, "kind": "pre"}]
        self.assertEqual(reference_trace(parse_case(self.document)), (0.5,))

    def test_pre_then_post_potentiates(self):
        self.document["events"] = [{"tick": 1, "kind": "pre"}, {"tick": 3, "kind": "post"}]
        a, b = reference_trace(parse_case(self.document))
        self.assertGreater(b, a)
        self.assertAlmostEqual(b, 0.5 + 0.07 * math.exp(-2 / 20))

    def test_post_then_pre_depresses(self):
        self.document["events"] = [{"tick": 1, "kind": "post"}, {"tick": 3, "kind": "pre"}]
        a, b = reference_trace(parse_case(self.document))
        self.assertLess(b, a)
        self.assertAlmostEqual(b, 0.5 - 0.08 * math.exp(-2 / 20))

    def test_no_cross_case_mutation(self):
        case = parse_case(self.document)
        before = reference_trace(case)
        self.document["events"].clear()
        self.assertEqual(reference_trace(case), before)

    def test_clipped_weight(self):
        self.document["initial_weight"] = 0.95
        self.document["events"] = [{"tick": 1, "kind": "pre"}, {"tick": 2, "kind": "post"}]
        self.assertEqual(reference_trace(parse_case(self.document))[-1], 1.0)

    def test_no_unknown_private_fields(self):
        self.document["genesis_identity"] = "PRIVATE"
        with self.assertRaises(STDPInputError):
            parse_case(self.document)

    def test_visibility_must_be_public_synthetic(self):
        for visibility in ("PRIVATE", "PUBLIC", "SECRET", "public_synthetic", None):
            with self.subTest(visibility=visibility):
                self.document["visibility"] = visibility
                with self.assertRaises(STDPInputError):
                    parse_case(self.document)

    def test_event_unknown_key_rejected(self):
        self.document["events"][0]["real_sensor"] = "NO"
        with self.assertRaises(STDPInputError):
            parse_case(self.document)

    def test_duplicate_or_unsorted_or_same_tick_refused(self):
        for ticks in ([1, 1], [2, 1], [0, 1], [1, 4097]):
            with self.subTest(ticks=ticks):
                d = public_fixture()
                d["events"] = [{"tick": t, "kind": "pre"} for t in ticks]
                with self.assertRaises(STDPInputError):
                    parse_case(d)

    def test_bad_events_rejected(self):
        for values in ([], [1], [{"tick": True, "kind": "pre"}],
                       [{"tick": 1, "kind": "other"}],
                       [{"tick": 1.0, "kind": "pre"}],
                       [{"tick": t, "kind": "pre"} for t in range(1, 258)]):
            with self.subTest(count=len(values)):
                d = public_fixture()
                d["events"] = values
                with self.assertRaises(STDPInputError):
                    parse_case(d)

    def test_numeric_invalid_values(self):
        for key, val in (("dt_ms", float("nan")), ("dt_ms", True),
                         ("tau_pre_ms", float("inf")), ("initial_weight", 2.0),
                         ("min_weight", 2), ("a_plus", -0.1),
                         ("a_minus", "0.2"), ("max_weight", 0)):
            with self.subTest(key=key):
                d = public_fixture()
                d[key] = val
                with self.assertRaises(STDPInputError):
                    parse_case(d)

    def test_reject_non_dict_payload(self):
        with self.assertRaises(STDPInputError):
            parse_case([])

    def test_tolerance_rejects_nonfinite(self):
        with self.assertRaises(STDPInputError):
            compare_native(parse_case(self.document), tolerance=float("nan"))

    def test_native_missing_fails_loudly(self):
        if importlib.util.find_spec("brian2") is None:
            with self.assertRaises(BackendUnavailable):
                brian2_trace(parse_case(self.document))


@unittest.skipUnless(importlib.util.find_spec("brian2"), "native Brian2 optional")
class STDPRealBrian2Tests(unittest.TestCase):
    def test_native_differential(self):
        proof = compare_native(parse_case(public_fixture()))
        self.assertTrue(proof["pass"])
        self.assertLessEqual(proof["max_abs_weight_error"], 1e-8)

    def test_native_potentiation_and_depression(self):
        for kinds in (("pre", "post"), ("post", "pre")):
            d = public_fixture()
            d["events"] = [{"tick": 1, "kind": kinds[0]},
                           {"tick": 3, "kind": kinds[1]}]
            self.assertTrue(compare_native(parse_case(d))["pass"])


if __name__ == "__main__":
    unittest.main()
