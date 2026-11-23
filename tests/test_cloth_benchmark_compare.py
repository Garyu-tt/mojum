import json
import tempfile
import unittest
from pathlib import Path
from tools.cloth_benchmark_compare import ReportError, SCHEMA, analyze, load_report, _rank, _pairs


def candidate(engine, **updates):
    item = {
        "engine": engine, "engine_version": "1.0", "scenario_id": "drape",
        "seed": 7, "mesh_sha256": "a" * 64, "vertices": 24, "constraints": 80,
        "dt_ns": 4_000_000, "steps": 6, "warmup_steps": 2,
        "cpu_samples_us": [10, 20, 30, 40], "rss_peak_bytes": 1_000_000,
        "penetration_count": 0, "dropped_steps": 0,
    }
    item.update(updates)
    return item


def fixture(runs=None):
    return {"schema": SCHEMA, "visibility": "PUBLIC_SYNTHETIC",
            "runs": runs or [candidate("mujoco"), candidate("pbd", cpu_samples_us=[12, 18, 28, 38])]}


class BenchmarkTests(unittest.TestCase):
    def test_deterministic_comparable_rank(self):
        result = analyze(fixture())
        self.assertTrue(result["comparable"])
        self.assertEqual([x["engine"] for x in result["performance_order_by_p95_then_rss"]], ["pbd", "mujoco"])
        self.assertEqual(result["input_sha256"], analyze(fixture())["input_sha256"])

    def test_mismatched_workload_is_not_ranked(self):
        result = analyze(fixture([candidate("m"), candidate("p", seed=8)]))
        self.assertFalse(result["comparable"])
        self.assertEqual(result["performance_order_by_p95_then_rss"], [])

    def test_mesh_mismatch_is_not_ranked(self):
        result = analyze(fixture([candidate("m"), candidate("p", mesh_sha256="b" * 64)]))
        self.assertFalse(result["comparable"])

    def test_percentile_nearest_rank(self):
        self.assertEqual(_rank([5, 1, 4, 2, 3], .50), 3)
        self.assertEqual(_rank([1, 2, 3, 4, 5], .95), 5)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ReportError):
            _pairs([("visibility", "PRIVATE"), ("visibility", "PUBLIC_SYNTHETIC")])

    def test_private_visibility_rejected(self):
        value = fixture()
        value["visibility"] = "PRIVATE"
        with self.assertRaises(ReportError):
            analyze(value)

    def test_boolean_integer_rejected(self):
        value = fixture()
        value["runs"][0]["seed"] = True
        with self.assertRaises(ReportError):
            analyze(value)

    def test_bad_sample_length_rejected(self):
        value = fixture()
        value["runs"][0]["cpu_samples_us"].pop()
        with self.assertRaises(ReportError):
            analyze(value)

    def test_nan_and_negative_sample_rejected(self):
        for sample in (float("nan"), float("inf"), -1):
            value = fixture()
            value["runs"][0]["cpu_samples_us"][0] = sample
            with self.subTest(sample=sample), self.assertRaises(ReportError):
                analyze(value)

    def test_unknown_keys_rejected(self):
        value = fixture()
        value["runs"][0]["fps"] = 60
        with self.assertRaises(ReportError):
            analyze(value)

    def test_run_and_sample_budgets(self):
        with self.assertRaises(ReportError):
            analyze(fixture([candidate(str(i)) for i in range(17)]))
        too_many = candidate("a", steps=1003, cpu_samples_us=[1] * 1001, warmup_steps=2)
        with self.assertRaises(ReportError):
            analyze(fixture([too_many, too_many]))

    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source.json"
            target.write_text(json.dumps(fixture()), encoding="utf-8")
            link = Path(directory) / "link.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink unavailable")
            with self.assertRaises(ReportError):
                load_report(str(link))

    def test_cli_never_overwrites(self):
        from tools.cloth_benchmark_compare import main
        with tempfile.TemporaryDirectory() as directory:
            src, dst = Path(directory) / "in.json", Path(directory) / "out.json"
            src.write_text(json.dumps(fixture()), encoding="utf-8")
            dst.write_text("keep", encoding="utf-8")
            self.assertEqual(main([str(src), "--output", str(dst)]), 2)
            self.assertEqual(dst.read_text(encoding="utf-8"), "keep")


    def test_deeply_nested_json_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep.json"
            path.write_text("[" * 12000 + "0" + "]" * 12000, encoding="utf-8")
            with self.assertRaises(ReportError):
                load_report(str(path))

    def test_duplicate_json_keys_keep_specific_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
            with self.assertRaisesRegex(ReportError, "duplicate JSON object key"):
                load_report(str(path))

    def test_oversized_integer_token_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "integer.json"
            path.write_text("{" + '"value":' + ("9" * 5000) + "}", encoding="utf-8")
            with self.assertRaises(ReportError):
                load_report(str(path))


if __name__ == "__main__":
    unittest.main()
