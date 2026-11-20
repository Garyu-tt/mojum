"""Independent QA: reject goal-free CLI overrides before creating a journal.

Also keep the public proprioception validation contract stable for huge integers
that make CPython's math.isfinite raise OverflowError.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.developmental_policy import DevelopmentError, Proprioception


ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 64


class GoalFreeInputBoundaryTests(unittest.TestCase):
    @staticmethod
    def run_cli(db, *extra):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [
                sys.executable, "-m", "mojum.aquarium",
                "--db", str(db), "--ticks", "1",
                "--exploration-mode", "goal_free",
                *extra,
            ],
            cwd=ROOT, env=env, text=True, capture_output=True,
            timeout=30, check=False,
        )

    def test_invalid_override_does_not_create_private_journal(self):
        # Prior ordering constructed Aquarium and wrote its bootstrap first.
        for args in (("--dx", "1.0"), ("--dy", ".4"),
                     ("--dx", "nan"), ("--dy", "inf")):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "must-not-exist.sqlite3"
                outcome = self.run_cli(db, *args)
                self.assertEqual(outcome.returncode, 2, outcome.stderr)
                self.assertIn("cannot override goal-free", outcome.stderr)
                self.assertFalse(db.exists())
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_invalid_override_does_not_touch_existing_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "existing.sqlite3"
            accepted = self.run_cli(db)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertTrue(db.is_file())
            before = db.read_bytes()
            before_files = sorted(p.name for p in Path(tmp).iterdir())
            outcome = self.run_cli(db, "--dy", ".5")
            self.assertEqual(outcome.returncode, 2, outcome.stderr)
            self.assertEqual(db.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), before_files)

    def test_huge_integer_proprioception_fails_as_development_error(self):
        gigantic = 10 ** 5000
        for x, y in ((gigantic, 1), (1, gigantic)):
            with self.subTest(which="x" if x == gigantic else "y"):
                with self.assertRaisesRegex(
                    DevelopmentError, "invalid numeric proprioception",
                ):
                    Proprioception(0, REVISION, x, y, False)

    def test_regular_numeric_proprioception_remains_supported(self):
        signal = Proprioception(0, REVISION, 1, 2, True)
        self.assertEqual(signal.public()["position_xy"], [1.0, 2.0])
        self.assertTrue(signal.public()["heartbeat_pulse"])


if __name__ == "__main__":
    unittest.main()
