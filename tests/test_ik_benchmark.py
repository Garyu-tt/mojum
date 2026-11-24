import json
import unittest

from tools.ik_benchmark import report_json, run_benchmark, solve_ccd, solve_two_link


class IKBENCHMARKTests(unittest.TestCase):
    def test_analytic_two_link_reaches_target(self):
        result = solve_two_link((1.0, 1.0), (1.0, 1.0),
                                ((-3.14, 3.14), (-3.14, 3.14)))
        self.assertTrue(result.converged)
        self.assertLessEqual(result.residual_m, 1e-6)
        self.assertEqual(result.iterations, 1)

    def test_ccd_reaches_reachable_target_deterministically(self):
        args = ((1.0, 1.0, 0.5), (1.2, 0.8),
                ((-3.14, 3.14), (-3.14, 3.14), (-3.14, 3.14)))
        a = solve_ccd(*args, max_iterations=512)
        b = solve_ccd(*args, max_iterations=512)
        self.assertEqual(a.digest_sha256, b.digest_sha256)
        self.assertLessEqual(a.residual_m, 1e-6)

    def test_ccd_respects_joint_limits(self):
        result = solve_ccd((1.0, 1.0), (1.0, 1.0),
                           ((-0.2, 0.2), (-0.2, 0.2)), max_iterations=20)
        self.assertTrue(all(-0.2 <= q <= 0.2 for q in result.joint_angles_rad))
        self.assertFalse(result.converged)

    def test_unreachable_analytic_target_rejected(self):
        with self.assertRaisesRegex(ValueError, "unreachable"):
            solve_two_link((1.0, 1.0), (3.0, 0.0),
                           ((-3.14, 3.14), (-3.14, 3.14)))

    def test_invalid_inputs_and_bool_rejected(self):
        with self.assertRaises(ValueError):
            solve_ccd((True, 1.0), (1.0, 1.0),
                      ((-3.14, 3.14), (-3.14, 3.14)))
        with self.assertRaises(ValueError):
            solve_ccd((1.0, 1.0), (float("nan"), 0.0),
                      ((-3.14, 3.14), (-3.14, 3.14)))
        with self.assertRaises(ValueError):
            solve_ccd((1.0, 1.0), (1.0, 1.0),
                      ((1.0, 1.0), (-3.14, 3.14)))

    def test_iteration_and_joint_budgets(self):
        with self.assertRaises(ValueError):
            solve_ccd((1.0,), (0.0, 0.0), ((-1.0, 1.0),), max_iterations=5000)
        with self.assertRaises(ValueError):
            solve_ccd(tuple([1.0] * 17), (0.0, 0.0),
                      tuple([(-1.0, 1.0)] * 17))

    def test_benchmark_report_has_both_solvers_and_resource_observations(self):
        rows = run_benchmark([{"case_id": "reachable", "lengths": [1.0, 1.0],
            "target_xy": [1.0, 1.0], "limits": [[-3.14, 3.14], [-3.14, 3.14]]}])
        self.assertEqual({row.solver for row in rows}, {"ccd", "two_link_analytic"})
        self.assertTrue(all(row.elapsed_ns >= 0 and row.peak_bytes >= 0 for row in rows))
        payload = json.loads(report_json(rows))
        self.assertEqual(payload["schema"], "mojum.ik-benchmark/1")
        self.assertIn("not rigid-body physics", payload["notice"])

    def test_duplicate_case_ids_rejected(self):
        case = {"case_id": "same", "lengths": [1.0, 1.0],
                "target_xy": [1.0, 1.0], "limits": [[-3.14, 3.14], [-3.14, 3.14]]}
        with self.assertRaises(ValueError):
            run_benchmark([case, case])


if __name__ == "__main__":
    unittest.main()
