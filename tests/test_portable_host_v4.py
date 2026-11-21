"""Acceptance QA for opt-in portable-host v4 without changing legacy host ABI."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from mojum.aquarium import Aquarium, V4_MODE
from mojum.portable_host import PortableHostError, run_paced


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class EpochHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "portable_v4_PRIVATE.sqlite3"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

    def cli(self, *extra):
        command = [
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(self.db), "--ticks", "2", "--interval-ms", "20",
            "--motor-mode", V4_MODE, "--sensory-mode", "none",
            "--temporal-mode", "none", "--neural-mode", "disabled", *extra,
        ]
        return subprocess.run(
            command, cwd=ROOT, env=self.env, text=True,
            capture_output=True, timeout=30,
        )

    def test_v4_pacing_accepts_no_toy_sensor(self):
        now = [0.0]

        def wait(delay):
            now[0] += delay

        with Aquarium(
            self.db, SEED, GRAPH, motor_mode=V4_MODE,
            sensory_mode="none", neural_mode="disabled",
        ) as aquarium:
            self.assertIsNone(aquarium.sensory_bridge)
            completed = run_paced(
                aquarium, ticks=2, interval_ms=20,
                stop_event=threading.Event(),
                monotonic=lambda: now[0], wait=wait,
            )
            self.assertEqual(completed, 2)
            self.assertEqual(aquarium.tick, 2)
            self.assertEqual(aquarium.store.count(), 3)

    def test_v4_marker_budget_rejects_whole_batch_before_writing(self):
        class NearBoundary:
            motor_mode = V4_MODE
            motor = object()
            sensory_bridge = None
            sensory_mode = "none"
            temporal_mode = "none"
            journal_event_budget = 3

            def __init__(self):
                self.store = self
                self.steps = 0

            def count(self):
                return 2

            def required_events_for_steps(self, ticks):
                assert ticks == 1
                return 2  # boundary marker plus frame

            def step(self):
                self.steps += 1

        runner = NearBoundary()
        with self.assertRaisesRegex(PortableHostError, "3-event"):
            run_paced(
                runner, ticks=1, interval_ms=20,
                stop_event=threading.Event(),
            )
        self.assertEqual(runner.steps, 0)

    def test_v4_cli_restart_keeps_absolute_ticks_and_private_output(self):
        first = self.cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        one = json.loads(first.stdout)
        self.assertEqual(one["schema"], "mojum.portable-host.run/3")
        self.assertEqual(one["motor_mode"], V4_MODE)
        self.assertEqual(one["sensory_mode"], "none")
        self.assertEqual((one["start_tick"], one["end_tick"]), (0, 2))
        self.assertEqual(one["journal_events"], 3)
        self.assertEqual(one["journal_trust"], "checksum_only_not_authenticated")
        self.assertNotIn("genome", first.stdout)
        second = self.cli()
        self.assertEqual(second.returncode, 0, second.stderr)
        two = json.loads(second.stdout)
        self.assertEqual((two["start_tick"], two["end_tick"]), (2, 4))
        self.assertEqual(two["journal_events"], 5)
        self.assertNotEqual(one["journal_head"], two["journal_head"])
        with Aquarium(
            self.db, SEED, GRAPH, motor_mode=V4_MODE,
            sensory_mode="none", neural_mode="disabled",
        ) as aquarium:
            self.assertEqual(aquarium.tick, 4)
            self.assertEqual(aquarium.checkpoint()["journal_head"], two["journal_head"])

    def test_v4_invalid_mode_pair_fails_before_journal_birth(self):
        invalid = (
            ("--sensory-mode", "toy_probe"),
            ("--temporal-mode", "range_guided"),
        )
        for args in invalid:
            with self.subTest(args=args):
                run = self.cli(*args)
                self.assertNotEqual(run.returncode, 0)
                self.assertEqual(run.stdout, "")
                self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
