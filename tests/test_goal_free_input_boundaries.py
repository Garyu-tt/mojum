"""Independent QA: reject goal-free CLI overrides before creating a journal.

Also keep the public proprioception validation contract stable for huge integers
that make CPython's math.isfinite raise OverflowError.
"""

import json
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

    def test_nominal_explicit_goal_free_values_rejected_without_bootstrap(self):
        # An explicit --dx 0.02 used to be silently accepted because it
        # equaled argparse's default, despite being an external motor input.
        candidates = (
            ("--dx", "0.02"), ("--dy", "0"),
            ("--dx", "0.02", "--dy", "0"),
            ("--dx", "-0.0"), ("--dy", "-0.0"),
        )
        for args in candidates:
            with self.subTest(args=args), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "never-created.sqlite3"
                outcome = self.run_cli(db, *args)
                self.assertEqual(outcome.returncode, 2, outcome.stderr)
                self.assertIn("cannot override goal-free", outcome.stderr)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_nominal_override_rejected_without_changing_existing_goal_free_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "existing.sqlite3"
            self.assertEqual(self.run_cli(db).returncode, 0)
            prior = db.read_bytes()
            files = sorted(p.name for p in Path(tmp).iterdir())
            for args in (("--dx", "0.02"), ("--dy", "0.0")):
                with self.subTest(args=args):
                    outcome = self.run_cli(db, *args)
                    self.assertEqual(outcome.returncode, 2, outcome.stderr)
                    self.assertEqual(db.read_bytes(), prior)
                    self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), files)

    def test_scripted_cli_default_and_explicit_nominal_values_remain_equivalent(self):
        # The new argparse omission sentinel is CLI-only: it must not alter
        # the default scripted movement or its event-chain bytes.
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            checkpoints = []
            for idx, opts in enumerate(((), ("--dx", "0.02", "--dy", "0.0"))):
                db = Path(tmp) / f"scripted-{idx}.sqlite3"
                completed = subprocess.run(
                    [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
                     "--ticks", "3", *opts],
                    cwd=ROOT, env=env, text=True, capture_output=True,
                    check=False, timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(db.exists())
                checkpoints.append(json.loads(completed.stdout))
            self.assertEqual(checkpoints[0], checkpoints[1])
            self.assertEqual(checkpoints[0]["stored_events"], 4)

    def test_invalid_scripted_displacement_does_not_bootstrap_sqlite(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        for movement in (("dx", "nan"), ("dy", "-inf"),
                         ("dx", "1001"), ("dy", "-1001")):
            with self.subTest(movement=movement), tempfile.TemporaryDirectory() as tmp:
                db = Path(tmp) / "invalid-scripted.sqlite3"
                completed = subprocess.run(
                    [sys.executable, "-m", "mojum.aquarium",
                     "--db", str(db), "--ticks", "1",
                     "--" + movement[0], movement[1]],
                    cwd=ROOT, env=env, text=True, capture_output=True,
                    timeout=30, check=False,
                )
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertIn("movement is nonfinite or outside", completed.stderr)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_invalid_scripted_displacement_preserves_existing_journal(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "existing.sqlite3"
            base = [sys.executable, "-m", "mojum.aquarium",
                    "--db", str(db), "--ticks", "1"]
            first = subprocess.run(
                base, cwd=ROOT, env=env, text=True, capture_output=True,
                timeout=30, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            previous = db.read_bytes()
            existing_files = sorted(p.name for p in Path(tmp).iterdir())
            failed = subprocess.run(
                [*base, "--dx", "nan"], cwd=ROOT, env=env,
                text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(failed.returncode, 2, failed.stderr)
            self.assertEqual(previous, db.read_bytes())
            self.assertEqual(existing_files, sorted(p.name for p in Path(tmp).iterdir()))

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
