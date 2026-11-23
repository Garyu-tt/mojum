"""No external packages or live data are required for these regression tests."""

import math
import unittest
from unittest.mock import patch

from mojum import pinocchio_benchmark as probe


class PinocchioReferenceTests(unittest.TestCase):
    def test_straight_arm(self):
        x, y = probe.reference_fk((0, 0))
        self.assertAlmostEqual(x, 0.6)
        self.assertAlmostEqual(y, 0)

    def test_right_angle_arm(self):
        x, y = probe.reference_fk((math.pi / 2, 0))
        self.assertAlmostEqual(x, 0, places=12)
        self.assertAlmostEqual(y, 0.6)

    def test_input_fails_closed(self):
        for value in ((True, 0), ("0", 0), (float("nan"), 0),
                      (float("inf"), 0), (2.9, 0), (0, 2.6), (1,),
                      [0, 0, 0], {"q": [0, 0]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.reference_fk(value)

    def test_benchmark_bounds(self):
        for samples in (False, -1, 0, 4097, 2.5):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                probe.run_benchmark(backend="reference", samples=samples)
        with self.assertRaises(ValueError):
            probe.run_benchmark(backend="external")

    def test_no_import_during_reference_path(self):
        with patch.object(probe, "_real_backend", side_effect=AssertionError("imported")):
            report = probe.run_benchmark(backend="reference", samples=8)
        self.assertEqual(report["schema"], "mojum.benchmark.pinocchio-2r/1")
        self.assertFalse(report["real_pinocchio_executed"])
        self.assertFalse(report["physical_contact"])
        self.assertEqual(len(report["cases"]), 4)
        self.assertTrue(all(case["fk_error_m"] == 0 for case in report["cases"]))

    def test_refuses_wrong_version(self):
        fake = type("WrongPin", (), {"__version__": "0.0.0"})()
        with patch.object(probe.importlib, "import_module", side_effect=[object(), fake]):
            with self.assertRaisesRegex(RuntimeError, "unsupported Pinocchio version"):
                probe._real_backend()

    def test_optional_real_backend_missing(self):
        with patch.object(probe, "_real_backend", side_effect=RuntimeError("pin==4.1.0 required")):
            with self.assertRaisesRegex(RuntimeError, "required"):
                probe.run_benchmark(backend="pinocchio")

    def test_deterministic_fields_without_timing(self):
        a = probe.run_benchmark(backend="reference", samples=8)
        b = probe.run_benchmark(backend="reference", samples=8)
        a.pop("fk_median_ns")
        b.pop("fk_median_ns")
        self.assertEqual(a, b)

    @unittest.skipUnless(__import__("importlib").util.find_spec("pinocchio"),
                         "optional pin==4.1.0 not installed")
    def test_real_pinocchio_if_installed(self):
        report = probe.run_benchmark(backend="pinocchio", samples=8)
        self.assertTrue(report["real_pinocchio_executed"])
        for case in report["cases"]:
            self.assertLess(case["ik_residual_m"], 1e-6)


if __name__ == "__main__":
    unittest.main()
