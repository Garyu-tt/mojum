"""Unit tests for the standalone SQLite warning diagnostic runner."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from sqlite_resourcewarning_probe import build_command


class ProbeTests(unittest.TestCase):
    def test_command_enables_tracemalloc_and_strict_warnings(self):
        cmd = build_command("python3", ["tests.test_example"])
        self.assertEqual(cmd, ["python3", "-X", "tracemalloc=25",
                               "-W", "error::ResourceWarning", "-m", "unittest",
                               "tests.test_example"])

    def test_rejects_unittest_option_injection(self):
        with self.assertRaises(ValueError):
            build_command(sys.executable, ["-k", "anything"])

    def test_rejects_empty_targets(self):
        with self.assertRaises(ValueError):
            build_command(sys.executable, [])

    def test_actual_warning_causes_nonzero_exit(self):
        # Exercise an actual ResourceWarning through the child interpreter.
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "test_warning_fixture.py"
            path.write_text(
                "import unittest\n"
                "class TestWarning(unittest.TestCase):\n"
                "    def test_warning(self):\n"
                "        import warnings\n"
                "        warnings.warn('unclosed database', ResourceWarning)\n",
                encoding="utf-8",
            )
            runner = ROOT / "tools" / "sqlite_resourcewarning_probe.py"
            result = subprocess.run(
                [sys.executable, str(runner), "--python", sys.executable,
                 "test_warning_fixture.TestWarning.test_warning"],
                cwd=tmp, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ResourceWarning", result.stderr)


if __name__ == "__main__":
    unittest.main()
