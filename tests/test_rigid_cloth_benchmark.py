import copy
import importlib.util
import math
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "integration_benchmarks" / "rigid_cloth_benchmark.py"
SPEC = importlib.util.spec_from_file_location("rigid_cloth_benchmark", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def fixture():
    return {
        "schema": MODULE.SCHEMA,
        "provenance": "PUBLIC_SYNTHETIC",
        "consent": True,
        "rig": {"asset_id": "fixture-rig", "revision": "r1", "dof": 12, "vertices": 240, "constraints": 480},
        "engine": {"name": "mujoco-flex", "version": "3.3.0", "threads": 1, "platform": "x86_64-linux"},
        "runs": [
            {"cpu_ms": 2.0, "rss_mib": 12.0, "ik_residual": None, "limit_violations": 0, "contact_penetration_mm": 0.1},
            {"cpu_ms": 4.0, "rss_mib": 14.0, "ik_residual": 0.002, "limit_violations": 0, "contact_penetration_mm": 0.2},
            {"cpu_ms": 3.0, "rss_mib": 13.0, "ik_residual": 0.001, "limit_violations": 1, "contact_penetration_mm": 0.0},
        ],
    }


class RigidClothBenchmarkTests(unittest.TestCase):
    def test_summary_is_deterministic_and_reports_not_verified(self):
        data = fixture()
        first = MODULE.summarize(data)
        second = MODULE.summarize(copy.deepcopy(data))
        self.assertEqual(first, second)
        self.assertEqual(first["cpu_ms"]["median"], 3.0)
        self.assertEqual(first["cpu_ms"]["p95"], 4.0)
        self.assertEqual(first["ik_residual"]["max"], 0.002)
        self.assertEqual(first["limit_violations"], 1)
        self.assertEqual(first["measurement_status"], "externally_measured_input_not_verified_by_this_tool")
        self.assertEqual(len(first["sha256"]), 64)

    def test_rejects_missing_consent_and_private_provenance(self):
        for field, value in (("consent", False), ("provenance", "PRIVATE")):
            data = fixture()
            data[field] = value
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.summarize(data)

    def test_rejects_boolean_numeric_and_nonfinite(self):
        for value in (True, float("nan"), float("inf"), -1):
            data = fixture()
            data["runs"][0]["cpu_ms"] = value
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.summarize(data)

    def test_rejects_bool_in_integer_fields(self):
        data = fixture()
        data["rig"]["dof"] = True
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.summarize(data)

    def test_rejects_unknown_engine_and_extra_keys(self):
        data = fixture()
        data["engine"]["name"] = "magic-engine"
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.summarize(data)
        data = fixture()
        data["extra"] = "no"
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.summarize(data)

    def test_rejects_empty_or_oversized_runs(self):
        for runs in ([], [fixture()["runs"][0]] * (MODULE.MAX_RUNS + 1)):
            data = fixture()
            data["runs"] = runs
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.summarize(data)

    def test_oversized_native_keys_are_rejected_before_hashing(self):
        data = fixture()
        data["x" * 1_000_000] = data.pop("schema")
        with self.assertRaisesRegex(MODULE.BenchmarkError, "bounded native strings"):
            MODULE.summarize(data)

        data = fixture()
        data["rig"]["x" * 1_000_000] = data["rig"].pop("asset_id")
        with self.assertRaisesRegex(MODULE.BenchmarkError, "bounded native strings"):
            MODULE.summarize(data)

        data = fixture()
        data["runs"][0]["x" * 1_000_000] = data["runs"][0].pop("cpu_ms")
        with self.assertRaisesRegex(MODULE.BenchmarkError, "bounded native strings"):
            MODULE.summarize(data)

    def test_input_is_not_mutated(self):
        data = fixture()
        before = copy.deepcopy(data)
        MODULE.summarize(data)
        self.assertEqual(data, before)

    def test_rejects_malformed_residual(self):
        data = fixture()
        data["runs"][0]["ik_residual"] = math.inf
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.summarize(data)


if __name__ == "__main__":
    unittest.main()
