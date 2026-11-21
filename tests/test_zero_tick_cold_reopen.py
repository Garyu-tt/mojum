"""CPU-only CLI regression: zero ticks must not append a frame on cold reopen."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ZeroTickCliTest(unittest.TestCase):
    def run_cli(self, db, ticks):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
             "--ticks", str(ticks), "--neural-mode", "shadow"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_zero_tick_cold_reopen_preserves_head_and_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "private.sqlite3"
            initial = self.run_cli(db, 0)
            self.assertEqual(initial["tick"], 0)
            self.assertEqual(initial["stored_events"], 1)
            self.assertEqual(initial["neural_mode"], "shadow")
            after_frames = self.run_cli(db, 2)
            self.assertEqual(after_frames["tick"], 2)
            self.assertEqual(after_frames["stored_events"], 3)
            self.assertNotEqual(after_frames["journal_head"], initial["journal_head"])
            reopened = self.run_cli(db, 0)
            self.assertEqual(reopened, after_frames)
            self.assertEqual(self.run_cli(db, 0), after_frames)


if __name__ == "__main__":
    unittest.main()
