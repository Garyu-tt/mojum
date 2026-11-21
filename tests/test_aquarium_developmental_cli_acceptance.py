"""Integration QA for the opt-in motor controller against current main CLI guards."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class MotorCliIntegrationTests(unittest.TestCase):
    def test_internal_mode_starts_without_external_displacement(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "motor.sqlite3"
            argv = [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
                    "--ticks", "4", "--motor-mode", "developmental_fixture"]
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            first = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            state = json.loads(first.stdout)
            self.assertEqual((state["tick"], state["stored_events"]), (4, 5))
            self.assertEqual(state["motor_mode"], "developmental_fixture")
            self.assertEqual(state["motor_state"]["observations"], 4)
            later = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(later.returncode, 0, later.stderr)
            reopened = json.loads(later.stdout)
            self.assertEqual((reopened["tick"], reopened["stored_events"]), (8, 9))

    def test_reject_explicit_external_motion_before_db_open(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "rejected.sqlite3"
            result = subprocess.run(
                [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
                 "--ticks", "0", "--motor-mode", "developmental_fixture", "--dx", "0.1"],
                cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
