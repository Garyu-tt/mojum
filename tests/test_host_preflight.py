"""Contract tests for the dependency-free offline host preflight."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from mojum import host_preflight


class HostPreflightTests(unittest.TestCase):
    def test_schema_and_safe_shape(self):
        report = host_preflight.collect()
        self.assertEqual(report["schema"], "mojum.host-preflight/1")
        self.assertTrue(report["offline"])
        self.assertTrue(report["advisory_only"])
        self.assertEqual(set(report["optional_modules"]), set(host_preflight.OPTIONAL_MODULES))
        self.assertNotIn("hostname", report)
        self.assertNotIn("home", report)
        self.assertNotIn("cwd", report)

    def test_missing_optional_modules_are_safe(self):
        with patch.object(host_preflight.importlib.util, "find_spec", side_effect=ImportError):
            self.assertTrue(all(v is False for v in host_preflight.collect()["optional_modules"].values()))

    def test_cpu_count_unknown(self):
        with patch.object(host_preflight.os, "cpu_count", return_value=None):
            self.assertIsNone(host_preflight.collect()["cpu_logical"])

    def test_cli_json_and_strict(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(host_preflight.main(["--strict"]), 0)
        self.assertEqual(json.loads(out.getvalue())["schema"], host_preflight.SCHEMA)

    def test_strict_rejects_unsupported_python(self):
        original = host_preflight.collect
        def unsupported():
            result = original()
            result["python"]["supported"] = False
            return result
        with patch.object(host_preflight, "collect", side_effect=unsupported):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(host_preflight.main(["--strict"]), 2)


if __name__ == "__main__":
    unittest.main()
