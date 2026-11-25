import copy
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

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
    def run_cli(self, path, *extra):
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), str(path), *map(str, extra)],
            capture_output=True, text=True, check=False, timeout=5,
        )

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

    def test_rejects_hostile_discriminator_values_without_comparison(self):
        class HostileValue:
            def __eq__(self, other):
                raise AssertionError("untrusted value equality must not execute")

        for field in ("schema", "provenance"):
            data = fixture()
            data[field] = HostileValue()
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.summarize(data)

    def test_rejects_boolean_numeric_and_nonfinite(self):
        for value in (True, float("nan"), float("inf"), -1):
            data = fixture()
            data["runs"][0]["cpu_ms"] = value
            with self.assertRaises(MODULE.BenchmarkError):
                MODULE.summarize(data)

    def test_oversized_integer_measurement_is_rejected(self):
        with self.assertRaises(MODULE.BenchmarkError):
            MODULE.summarize(dict(fixture(), runs=[dict(fixture()["runs"][0], cpu_ms=10 ** 1000)]))

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

    def test_hostile_string_subclass_keys_rejected_at_all_schema_levels(self):
        class HostileKey(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                raise AssertionError("untrusted key equality must not execute")

        data = fixture()
        data[HostileKey("schema")] = data.pop("schema")
        with self.assertRaisesRegex(MODULE.BenchmarkError, "bounded native strings"):
            MODULE.summarize(data)

        for field in ("rig", "engine", "runs"):
            data = fixture()
            mapping = data[field] if field != "runs" else data["runs"][0]
            key = next(iter(mapping))
            mapping[HostileKey(key)] = mapping.pop(key)
            with self.assertRaisesRegex(MODULE.BenchmarkError, "bounded native strings"):
                MODULE.summarize(data)

    def test_duplicate_json_object_keys_are_rejected(self):
        with self.assertRaisesRegex(MODULE.BenchmarkError, "duplicate JSON object key"):
            MODULE._unique_object([("schema", "one"), ("schema", "two")])

    def test_cli_bounds_input_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "too-large.json"
            path.write_bytes(b" " * (MODULE.MAX_INPUT_BYTES + 1))
            self.assertEqual(MODULE.main([str(path)]), 2)

    def test_cli_accepts_regular_file_with_quoted_brackets_and_escapes(self):
        data = fixture()
        data["rig"]["asset_id"] = 'quoted-"\\' + "[" * 25 + "]" * 25
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            run = self.run_cli(path)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), MODULE.summarize(data))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_cli_accepts_symlink_to_regular_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            link = Path(directory) / "fixture-link.json"
            try:
                link.symlink_to(path)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            output = Path(directory) / "summary.json"
            run = self.run_cli(link, "--output", output)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), MODULE.summarize(fixture()))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "POSIX FIFO support required")
    def test_cli_rejects_writerless_fifo_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "input.fifo"
            os.mkfifo(fifo)
            output = Path(directory) / "summary.json"
            run = self.run_cli(fifo, "--output", output)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("regular file", run.stderr)
            self.assertEqual(run.stdout, "")
            self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX device required")
    def test_cli_rejects_device_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.json"
            run = self.run_cli(os.devnull, "--output", output)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("regular file", run.stderr)
            self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX flag requirement")
    def test_missing_nonblock_is_rejected_before_open(self):
        with patch.object(MODULE.os, "O_NONBLOCK", 0, create=True), \
             patch.object(MODULE.os, "open") as opened, \
             redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(MODULE.main(["unused-input"]), 2)
        opened.assert_not_called()
        self.assertIn("O_NONBLOCK", errors.getvalue())

    def test_opened_descriptor_is_closed_on_admission_and_stream_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            original_open = os.open
            for failing_operation in ("fstat", "fdopen"):
                opened = []

                def record_open(*args, **kwargs):
                    descriptor = original_open(*args, **kwargs)
                    opened.append(descriptor)
                    return descriptor

                with self.subTest(operation=failing_operation), \
                     patch.object(MODULE.os, "open", side_effect=record_open), \
                     patch.object(MODULE.os, failing_operation, side_effect=OSError("injected failure")):
                    with self.assertRaisesRegex(OSError, "injected failure"):
                        MODULE._read_input(path)
                self.assertEqual(len(opened), 1)
                with self.assertRaises(OSError):
                    os.fstat(opened[0])

    def test_cli_rejects_deep_huge_integer_and_malformed_json_without_traceback(self):
        cases = {
            "deep": "[" * 12000 + "0" + "]" * 12000,
            "integer": "1" * 5000,
            "malformed": '{"unfinished":',
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.json"
            output.write_text("existing report", encoding="utf-8")
            for name, document in cases.items():
                with self.subTest(case=name):
                    path = Path(directory) / f"{name}.json"
                    path.write_text(document, encoding="utf-8")
                    run = self.run_cli(path, "--output", output)
                    self.assertEqual(run.returncode, 2, run.stderr)
                    self.assertIn("benchmark rejected:", run.stderr)
                    self.assertNotIn("Traceback", run.stderr)
                    self.assertEqual(run.stdout, "")
                    self.assertEqual(output.read_text(encoding="utf-8"), "existing report")

    def test_json_depth_limit_is_checked_before_decoding(self):
        at_limit = "[" * MODULE.MAX_JSON_DEPTH + "0" + "]" * MODULE.MAX_JSON_DEPTH
        MODULE._decode_document(at_limit.encode("utf-8"))
        with patch.object(MODULE.json, "loads", side_effect=AssertionError("decoder must not run")):
            with self.assertRaisesRegex(MODULE.BenchmarkError, "container levels"):
                MODULE._decode_document(("[" + at_limit + "]").encode("utf-8"))

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
