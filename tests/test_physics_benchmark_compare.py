import copy
import hashlib
import json
import unittest

from mojum.physics_benchmark_compare import (
    BenchmarkReportError, COMPARISON_SCHEMA, compare_json_reports,
    compare_reports, load_report_json,
)


def digest(label):
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def report(solver="analytic", samples=None):
    samples = [10, 20, 30, 40, 50] if samples is None else samples
    return {
        "schema": "mojum.physics-benchmark/1",
        "visibility": "PUBLIC_SYNTHETIC",
        "workload_sha256": digest("workload"),
        "rig_sha256": digest("rig"),
        "solver": solver,
        "model_sha256": digest("model-" + solver),
        "seed": 702,
        "timestep_ns": 16_666_667,
        "warmup_steps": 5,
        "measured_steps": len(samples),
        "platform": {"os": "linux", "arch": "x86_64",
                     "python": "3.13", "cpu_class": "generic-x86_64"},
        "step_time_ns": samples,
    }


class PhysicsBenchmarkCompareTests(unittest.TestCase):
    def test_exact_median_p95_and_reduced_ratio(self):
        result = compare_reports(report(samples=[50, 10, 40, 20, 30]),
                                 report("pinocchio", [100, 20, 80, 40, 60]))
        self.assertEqual(result["schema"], COMPARISON_SCHEMA)
        self.assertEqual(result["baseline"]["median_step_time_ns"],
                         {"numerator": 30, "denominator": 1})
        self.assertEqual(result["baseline"]["p95_step_time_ns"], 50)
        self.assertEqual(result["candidate_over_baseline_median"],
                         {"numerator": 2, "denominator": 1})

    def test_even_median_is_exact(self):
        result = compare_reports(report(samples=[10, 20, 21, 40]),
                                 report("jolt", [10, 20, 21, 40]))
        self.assertEqual(result["baseline"]["median_step_time_ns"],
                         {"numerator": 41, "denominator": 2})

    def test_p95_nearest_rank(self):
        values = [9, 1, 7, 3, 5, 11, 13, 15, 17, 19]
        result = compare_reports(report(samples=values), report("other", values))
        self.assertEqual(result["baseline"]["p95_step_time_ns"], 19)

    def test_solver_and_model_may_differ(self):
        result = compare_reports(report(), report("pinocchio"))
        self.assertTrue(result["comparable"])
        self.assertNotEqual(result["baseline"]["report_sha256"],
                            result["candidate"]["report_sha256"])

    def test_execution_envelope_mismatches_refused(self):
        for field in ("workload_sha256", "rig_sha256", "seed", "timestep_ns",
                      "warmup_steps", "measured_steps", "platform"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(report("candidate"))
                if field == "platform":
                    candidate[field]["cpu_class"] = "different"
                elif field.endswith("_sha256"):
                    candidate[field] = digest("different")
                else:
                    candidate[field] += 1
                with self.assertRaises(BenchmarkReportError):
                    compare_reports(report(), candidate)

    def test_schema_visibility_and_unknown_fields_refused(self):
        mutations = (
            lambda x: x.update(schema="mojum.physics-benchmark/2"),
            lambda x: x.update(visibility="PRIVATE"),
            lambda x: x.update(extra="surprise"),
            lambda x: x.pop("model_sha256"),
        )
        for mutate in mutations:
            item = report()
            mutate(item)
            with self.assertRaises(BenchmarkReportError):
                compare_reports(item, report("candidate"))

    def test_exact_types_and_bounds(self):
        for field, value in (("seed", True), ("timestep_ns", 0),
                             ("warmup_steps", -1), ("measured_steps", 2),
                             ("step_time_ns", [1, 2, True, 4, 5]),
                             ("step_time_ns", [1, 2, 3, 4, 10**13])):
            with self.subTest(field=field, value=value):
                item = report()
                item[field] = value
                with self.assertRaises(BenchmarkReportError):
                    compare_reports(item, report("candidate"))

    def test_sample_cardinality_is_exact(self):
        item = report()
        item["measured_steps"] = 6
        with self.assertRaises(BenchmarkReportError):
            compare_reports(item, report("candidate"))

    def test_hash_solver_and_platform_validation(self):
        mutations = (
            lambda x: x.update(model_sha256="A" * 64),
            lambda x: x.update(solver="../external"),
            lambda x: x["platform"].update(cpu_class=""),
            lambda x: x["platform"].update(hostname="private-host"),
        )
        for mutate in mutations:
            item = report()
            mutate(item)
            with self.assertRaises(BenchmarkReportError):
                compare_reports(item, report("candidate"))

    def test_receipt_deterministic_and_redacted(self):
        one = compare_reports(report(), report("candidate"))
        two = compare_reports(copy.deepcopy(report()),
                              copy.deepcopy(report("candidate")))
        self.assertEqual(one, two)
        serialized = json.dumps(one, sort_keys=True)
        self.assertNotIn('"step_time_ns":', serialized)
        self.assertNotIn("model_sha256", serialized)
        self.assertIn("comparison_sha256", one)

    def test_json_duplicate_keys_nonfinite_and_bad_root(self):
        for payload in (b'{"schema":"a","schema":"b"}',
                        b'{"value":NaN}', b'[]', b'{"broken":'):
            with self.subTest(payload=payload):
                with self.assertRaises(BenchmarkReportError):
                    load_report_json(payload)

    def test_json_byte_budget_and_input_type(self):
        with self.assertRaises(BenchmarkReportError):
            load_report_json(b" " * (256 * 1024 + 1))
        with self.assertRaises(BenchmarkReportError):
            load_report_json(123)

    def test_json_entrypoint(self):
        left = json.dumps(report(), separators=(",", ":"))
        right = json.dumps(report("candidate"), separators=(",", ":"))
        self.assertTrue(compare_json_reports(left, right)["comparable"])


if __name__ == "__main__":
    unittest.main()
