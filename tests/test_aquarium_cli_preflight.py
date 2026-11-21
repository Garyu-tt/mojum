"""CLI movement preflight regression: invalid input must not bootstrap SQLite."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class AquariumCliMovementPreflightTests(unittest.TestCase):
    def test_invalid_movement_does_not_create_journal(self):
        for flag, value in (("--dx", "nan"), ("--dy", "inf"),
                            ("--dx", "1001"), ("--dy", "-1001")):
            with self.subTest(flag=flag, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    db = Path(directory) / "new.sqlite3"
                    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
                    result = subprocess.run(
                        [sys.executable, "-m", "mojum.aquarium",
                         "--db", str(db), "--ticks", "1", flag, value],
                        cwd=ROOT, env=env, capture_output=True, text=True,
                        timeout=30, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("movement", result.stderr)
                    self.assertFalse(db.exists(), "invalid CLI input created a journal")


if __name__ == "__main__":
    unittest.main()
