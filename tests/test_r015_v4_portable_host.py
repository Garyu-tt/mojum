"""Portable-host QA for opt-in epoch-v4 without changing the legacy host default."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, V4_MODE
from mojum.portable_host import PortableHostError, run_paced

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class EpochV4PortableHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "private-v4.sqlite3"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

    def command(self, *extra):
        return [
            sys.executable, "-m", "mojum.portable_host", "--db", str(self.db),
            "--ticks", "3", "--interval-ms", "20",
            "--neural-mode", "disabled", "--motor-mode", V4_MODE,
            *extra,
        ]

    def invoke(self, *extra):
        return subprocess.run(
            self.command(*extra), cwd=ROOT, env=self.env,
            capture_output=True, text=True, timeout=30,
        )

    def open_v4(self):
        return Aquarium(self.db, SEED, GRAPH, motor_mode=V4_MODE,
                        neural_mode="disabled")

    def test_wrong_cli_mode_combinations_cannot_birth_journal(self):
        for options in (
            (),
            ("--sensory-mode", "none"),
            ("--sensory-mode", "toy_probe", "--temporal-mode", "none"),
            ("--sensory-mode", "none", "--temporal-mode", "none",
             "--brain-mode", "developmental_controller"),
        ):
            with self.subTest(options=options):
                outcome = self.invoke(*options)
                self.assertNotEqual(outcome.returncode, 0)
                self.assertFalse(self.db.exists(), outcome.stderr)

    def test_v4_cli_resume_and_single_private_journal(self):
        args = ("--sensory-mode", "none", "--temporal-mode", "none")
        initial = self.invoke(*args)
        self.assertEqual(initial.returncode, 0, initial.stderr)
        one = json.loads(initial.stdout)
        self.assertEqual((one["start_tick"], one["end_tick"],
                          one["journal_events"]), (0, 3, 4))
        self.assertEqual((one["motor_mode"], one["sensory_mode"]),
                         (V4_MODE, "none"))
        self.assertNotIn("movement_request", initial.stdout)
        self.assertNotIn("genome", initial.stdout)
        with self.open_v4() as aquarium:
            self.assertEqual(aquarium.tick, 3)
            self.assertEqual(aquarium.checkpoint()["journal_head"],
                             one["journal_head"])
        subsequent = self.invoke(*args)
        self.assertEqual(subsequent.returncode, 0, subsequent.stderr)
        two = json.loads(subsequent.stdout)
        self.assertEqual((two["start_tick"], two["end_tick"],
                          two["journal_events"]), (3, 6, 7))
        with self.open_v4() as aquarium:
            self.assertEqual(aquarium.tick, 6)
            self.assertEqual(aquarium.checkpoint()["journal_head"],
                             two["journal_head"])

    def test_v4_host_reserves_epoch_marker_budget_before_any_step(self):
        with self.open_v4() as aquarium:
            original = aquarium.checkpoint()
            with patch.object(aquarium, "required_events_for_steps",
                              return_value=2), patch.object(
                                  aquarium.store, "count", return_value=99999):
                with self.assertRaisesRegex(PortableHostError, "100000-event"):
                    run_paced(aquarium, ticks=1, interval_ms=20,
                              stop_event=threading.Event())
            self.assertEqual(aquarium.checkpoint(), original)
            self.assertEqual(aquarium.store.count(), 1)

    def test_v4_source_cap_refusal_is_wrapped_and_nonmutating(self):
        with self.open_v4() as aquarium:
            original = aquarium.checkpoint()
            with patch.object(aquarium, "required_events_for_steps",
                              side_effect=AquariumError("source event budget exhausted")):
                with self.assertRaisesRegex(PortableHostError, "source event budget"):
                    run_paced(aquarium, ticks=1, interval_ms=20,
                              stop_event=threading.Event())
            self.assertEqual(aquarium.checkpoint(), original)
            self.assertEqual(aquarium.store.count(), 1)

    def test_v4_paced_steps_use_internal_policy(self):
        now = [0.0]
        def wait(delay):
            now[0] += delay
        with self.open_v4() as aquarium:
            completed = run_paced(
                aquarium, ticks=3, interval_ms=20,
                stop_event=threading.Event(), monotonic=lambda: now[0],
                wait=wait,
            )
            self.assertEqual(completed, 3)
            self.assertEqual(aquarium.tick, 3)
            self.assertEqual(aquarium.stored_events, 4)


if __name__ == "__main__":
    unittest.main()
