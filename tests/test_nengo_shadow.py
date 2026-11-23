"""No-dependency boundary and analytic tests; real Nengo smoke is conditional."""

import copy
import importlib.util
import math
import unittest
from unittest.mock import patch

from mojum.nengo_shadow import (
    DT_NS, INPUT_SCHEMA, MAX_ERROR_HZ, NengoShadowError,
    reference_rates, run_shadow, validate_fixture,
)


def fixture():
    return {
        "schema": INPUT_SCHEMA, "source": "PUBLIC_SYNTHETIC", "seed": 11,
        "dt_ns": DT_NS,
        "inputs_milli": [-1000, 0, 500, 1000],
        "gain_milli": [1000, 1500, 2000],
        "bias_milli": [2000, 1000, 0],
    }


class NengoShadowContractTests(unittest.TestCase):
    def test_non_mutating_and_detached(self):
        raw = fixture()
        checked = validate_fixture(raw)
        self.assertEqual(raw, checked)
        checked["gain_milli"].append(4)
        self.assertEqual(len(raw["gain_milli"]), 3)

    def test_stable_analytic_rates_bounded(self):
        result = reference_rates(fixture())
        self.assertEqual(len(result), 4)
        self.assertEqual(len(result[0]), 3)
        self.assertEqual(result[0][1], 0.0)
        self.assertEqual(result[1][1], 0.0)
        self.assertGreater(result[3][0], result[2][0])
        self.assertTrue(all(math.isfinite(v) and 0 <= v <= 500 for row in result for v in row))
        self.assertEqual(result, reference_rates(fixture()))

    def test_rejects_invalid_fixture_inputs(self):
        cases = [
            ("schema", "wrong"), ("source", "PRIVATE"), ("source", "PUBLIC"),
            ("seed", True), ("seed", -1), ("seed", 2**32),
            ("dt_ns", False), ("dt_ns", 2_000_000),
            ("inputs_milli", []), ("inputs_milli", [0] * 17),
            ("inputs_milli", [0, True]), ("inputs_milli", [float("nan")]),
            ("inputs_milli", [1001]), ("inputs_milli", [-1001]),
            ("gain_milli", [1000] * 9), ("gain_milli", [-1]),
            ("bias_milli", [1]), ("bias_milli", [4001, 1, 1]),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=repr(value)):
                case = fixture()
                case[key] = value
                with self.assertRaises(NengoShadowError):
                    validate_fixture(case)

    def test_rejects_unknown_fields_and_non_dict(self):
        for value in [None, [], 17, {**fixture(), "secret": "token"}]:
            with self.subTest(value=repr(value)):
                with self.assertRaises(NengoShadowError):
                    validate_fixture(value)

    def test_explicit_opt_in_before_dependency_access(self):
        with patch("mojum.nengo_shadow._pinned_nengo", side_effect=AssertionError("backend loaded")):
            with self.assertRaisesRegex(NengoShadowError, "explicit"):
                run_shadow(fixture())
            with self.assertRaisesRegex(NengoShadowError, "explicit"):
                run_shadow(fixture(), permit_nengo=1)

    def test_missing_or_unreviewed_dependency(self):
        with patch("mojum.nengo_shadow.metadata.version", side_effect=__import__("importlib").metadata.PackageNotFoundError("nengo")):
            with self.assertRaisesRegex(NengoShadowError, "not installed"):
                run_shadow(fixture(), permit_nengo=True)
        with patch("mojum.nengo_shadow.metadata.version", return_value="999.0"):
            with self.assertRaisesRegex(NengoShadowError, "unreviewed"):
                run_shadow(fixture(), permit_nengo=True)

    @unittest.skipUnless(importlib.util.find_spec("nengo"), "optional nengo not installed")
    def test_real_nengo_cpu_differential(self):
        from importlib.metadata import version
        if version("nengo") != "4.1.0":
            self.skipTest("unsupported Nengo version")
        one = run_shadow(fixture(), permit_nengo=True)
        two = run_shadow(copy.deepcopy(fixture()), permit_nengo=True)
        self.assertEqual(one, two)
        self.assertLessEqual(one["max_abs_error_hz"], MAX_ERROR_HZ)
        self.assertEqual(one["authority"], "SHADOW_ONLY")
        self.assertIsNone(one["motor_intention"])


if __name__ == "__main__":
    unittest.main()
