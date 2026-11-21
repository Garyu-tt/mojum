"""End-to-end CLI smoke test for the released synthetic CPU aquarium.

This checks subprocess startup and SQLite reopen, not MuJoCo or a learned brain.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AquariumCliSmokeTests(unittest.TestCase):
    def invoke(self, db, *, ticks=2, mode="shadow"):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
             "--ticks", str(ticks), "--neural-mode", mode],
            cwd=ROOT, env=env, text=True, capture_output=True,
            timeout=45, check=False,
        )

    def test_new_journal_reopen_and_mode_pin(self):
        with tempfile.TemporaryDirectory(prefix="mojum-cli-smoke-") as tmp:
            db = Path(tmp) / "synthetic-private.sqlite3"
            first = self.invoke(db)
            self.assertEqual(first.returncode, 0, first.stderr)
            a = json.loads(first.stdout)
            self.assertEqual((a["tick"], a["stored_events"]), (2, 3))
            self.assertEqual(a["neural_mode"], "shadow")
            self.assertIn("SYNTHETIC", a["status"])
            self.assertIn("NOT_FULL_BRAIN_OR_MUJOCO", a["status"])
            self.assertTrue(db.is_file())
            original_head = a["journal_head"]

            second = self.invoke(db, ticks=1)
            self.assertEqual(second.returncode, 0, second.stderr)
            b = json.loads(second.stdout)
            self.assertEqual((b["tick"], b["stored_events"]), (3, 4))
            self.assertNotEqual(original_head, b["journal_head"])

            mismatched = self.invoke(db, ticks=0, mode="disabled")
            self.assertNotEqual(mismatched.returncode, 0)
            verified = self.invoke(db, ticks=0)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            c = json.loads(verified.stdout)
            self.assertEqual((c["tick"], c["stored_events"]), (3, 4))
            self.assertEqual(c["journal_head"], b["journal_head"])


if __name__ == "__main__":
    unittest.main()
