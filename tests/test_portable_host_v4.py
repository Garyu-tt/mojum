"""Opt-in epoch-v4 foreground host admission without legacy status ABI changes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.portable_host import PortableHostError, run_paced, SCHEMA, SCHEMA_V4


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class V4PortableHostAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "private.sqlite3"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

    def invoke(self, *options):
        return subprocess.run(
            [sys.executable, "-m", "mojum.portable_host", "--db", str(self.db),
             "--ticks", "2", "--interval-ms", "20", *options],
            env=self.env, cwd=ROOT, text=True, capture_output=True, timeout=30,
        )

    def v4_options(self):
        return ("--motor-mode", V4_MODE, "--sensory-mode", "none",
                "--temporal-mode", "none", "--neural-mode", "disabled")

    def test_legacy_default_output_still_uses_exact_v2_status(self):
        run = self.invoke()
        self.assertEqual(run.returncode, 0, run.stderr)
        status = json.loads(run.stdout)
        self.assertEqual(status["schema"], SCHEMA)
        self.assertEqual((status["start_tick"], status["end_tick"]), (0, 2))
        self.assertEqual(status["journal_events"], 3)
        self.assertNotIn("motor_mode", status)
        self.assertNotIn("sensory_mode", status)
        self.assertNotIn("epoch_index", status)

    def test_v4_cli_reopens_private_journal_and_separates_run_status(self):
        first = self.invoke(*self.v4_options())
        self.assertEqual(first.returncode, 0, first.stderr)
        one = json.loads(first.stdout)
        self.assertEqual(one["schema"], SCHEMA_V4)
        self.assertEqual(one["motor_mode"], V4_MODE)
        self.assertEqual(one["sensory_mode"], "none")
        self.assertEqual((one["start_tick"], one["end_tick"]), (0, 2))
        self.assertEqual(one["journal_events"], 3)
        self.assertEqual((one["epoch_index"], one["epoch_tick"]), (0, 2))
        second = self.invoke(*self.v4_options())
        self.assertEqual(second.returncode, 0, second.stderr)
        two = json.loads(second.stdout)
        self.assertEqual((two["start_tick"], two["end_tick"]), (2, 4))
        self.assertEqual(two["journal_events"], 5)
        self.assertNotEqual(two["journal_head"], one["journal_head"])
        wire = inspect_journal(self.db, external_expected_head=two["journal_head"])
        self.assertEqual(wire["format"], V4_MODE)
        self.assertEqual((wire["frames"], wire["events"]), (4, 5))

    def test_invalid_mode_combinations_do_not_birth_sqlite_journal(self):
        for options in (
            ("--motor-mode", V4_MODE),
            ("--motor-mode", V4_MODE, "--sensory-mode", "none",
             "--temporal-mode", "range_guided"),
            (*self.v4_options(), "--brain-mode", "developmental_controller"),
            ("--sensory-mode", "none"),
        ):
            with self.subTest(options=options):
                run = self.invoke(*options)
                self.assertNotEqual(run.returncode, 0, run.stdout)
                self.assertFalse(self.db.exists(), run.stderr)

    def test_v4_default_shadow_neural_mode_is_valid(self):
        # The v4 host constrains sensory/temporal/brain modes, not the
        # independently versioned optional neural shadow computation.
        run = self.invoke("--motor-mode", V4_MODE, "--sensory-mode", "none",
                          "--temporal-mode", "none")
        self.assertEqual(run.returncode, 0, run.stderr)
        status = json.loads(run.stdout)
        self.assertEqual(status["schema"], SCHEMA_V4)
        self.assertEqual((status["start_tick"], status["end_tick"]), (0, 2))

    def test_v4_host_checks_budget_before_any_frame_or_epoch_marker(self):
        with Aquarium(
            self.db, SEED, GRAPH,
            motor_mode=V4_MODE, sensory_mode="none", temporal_mode="none",
            neural_mode="disabled",
        ) as aquarium:
            before = aquarium.checkpoint()
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", aquarium.store.count()):
                with self.assertRaisesRegex(PortableHostError, "requested ticks exceed"):
                    run_paced(aquarium, ticks=1, interval_ms=20,
                              stop_event=threading.Event())
            self.assertEqual(aquarium.checkpoint(), before)
            self.assertEqual(aquarium.store.count(), 1)


if __name__ == "__main__":
    unittest.main()
