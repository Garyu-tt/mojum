"""CLI movement preflight regression: invalid input must not bootstrap SQLite."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class AquariumCliMovementPreflightTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "new.sqlite3"

    def run_cli(self, ticks, *options):
        return subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
             "--ticks", str(ticks), *options],
            cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, text=True, timeout=30,
        )

    def test_invalid_movement_does_not_create_journal(self):
        for flag, value in (("--dx", "nan"), ("--dy", "inf"),
                            ("--dx", "1001"), ("--dy", "-1001")):
            with self.subTest(flag=flag, value=value):
                result = self.run_cli(1, flag, value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("movement", result.stderr)
                self.assertFalse(self.db.exists())

    def test_over_budget_diagonal_rejected_before_bootstrap(self):
        for ticks in (0, 1):
            with self.subTest(ticks=ticks):
                result = self.run_cli(ticks, "--dx", "600", "--dy", "-801")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("movement", result.stderr)
                self.assertEqual(list(self.db.parent.iterdir()), [])

    def test_limit_diagonal_commits_and_rejected_vector_preserves_checkpoint(self):
        accepted = self.run_cli(1, "--dx", "600", "--dy", "-800")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        checkpoint = json.loads(accepted.stdout)
        self.assertEqual((checkpoint["tick"], checkpoint["stored_events"]), (1, 2))

        rejected = self.run_cli(2, "--dx", "600", "--dy", "-801")
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        reopened = self.run_cli(0)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        self.assertEqual(json.loads(reopened.stdout), checkpoint)


if __name__ == "__main__":
    unittest.main()
