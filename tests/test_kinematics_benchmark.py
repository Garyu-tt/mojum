import copy
import unittest

from mojum.kinematics_benchmark import (
    BenchmarkValidationError,
    build_benchmark_report,
    verify_benchmark_report,
)


def sample(**overrides):
    values = dict(
        rig_id="humanoid-public-fixture", rig_revision="rig-r01", dof_count=24,
        solver_name="reference-fk-ik", solver_version="0.1.0", solver_license="Apache-2.0",
        case_id="standing-reach", seed=42, fk_samples=100,
        fk_max_position_error_um=12, ik_attempts=50, ik_converged=48,
        ik_max_residual_um=80, joint_limit_violations=0, contact_events=4,
        cpu_p50_us=1200, cpu_p95_us=1900, max_rss_kib=8192,
    )
    values.update(overrides)
    return build_benchmark_report(**values)


class KinematicsBenchmarkTests(unittest.TestCase):
    def test_valid_report_is_deterministic_and_verifiable(self):
        first, second = sample(), sample()
        self.assertEqual(first, second)
        self.assertTrue(verify_benchmark_report(first))
        self.assertEqual(first["contacts"]["authority"], "SIMULATED_ONLY")

    def test_input_strings_do_not_alias_report(self):
        source = "fixture"
        report = sample(rig_id=source)
        source = "changed"
        self.assertEqual(report["rig"]["id"], "fixture")

    def test_tampering_is_detected(self):
        report = sample()
        report["resources"]["cpu_p95_us"] += 1
        with self.assertRaises(BenchmarkValidationError):
            verify_benchmark_report(report)

    def test_unknown_fields_rejected(self):
        report = sample()
        report["private_payload"] = "must not pass"
        with self.assertRaises(BenchmarkValidationError):
            verify_benchmark_report(report)

    def test_bool_is_not_integer(self):
        with self.assertRaises(BenchmarkValidationError):
            sample(seed=True)

    def test_nonfinite_or_wrong_numeric_types_rejected(self):
        with self.assertRaises(BenchmarkValidationError):
            sample(cpu_p50_us=1.5)
        with self.assertRaises(BenchmarkValidationError):
            sample(dof_count=float("nan"))

    def test_ik_convergence_cannot_exceed_attempts(self):
        with self.assertRaises(BenchmarkValidationError):
            sample(ik_attempts=3, ik_converged=4)

    def test_percentile_order_is_enforced(self):
        with self.assertRaises(BenchmarkValidationError):
            sample(cpu_p50_us=2000, cpu_p95_us=1000)

    def test_bounds_are_enforced(self):
        with self.assertRaises(BenchmarkValidationError):
            sample(dof_count=513)
        with self.assertRaises(BenchmarkValidationError):
            sample(fk_samples=0)

    def test_contact_authority_cannot_be_relabelled(self):
        report = sample()
        report["contacts"]["authority"] = "PHYSICAL_HARDWARE"
        # A forged digest must not make a different authority admissible.
        import hashlib, json
        payload = {k: v for k, v in report.items() if k != "sha256"}
        report["sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        ).hexdigest()
        with self.assertRaises(BenchmarkValidationError):
            verify_benchmark_report(report)

    def test_detached_nested_output(self):
        report = sample()
        clone = copy.deepcopy(report)
        clone["solver"]["name"] = "mutated"
        self.assertNotEqual(report["solver"]["name"], clone["solver"]["name"])


if __name__ == "__main__":
    unittest.main()
