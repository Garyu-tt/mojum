"""Opt-in Local Cell/3-escape Aquarium mode: versioning, replay and bounds.

The escape profile exists because Local Cell/2 and /3 can learn themselves
into permanent rest after every move in a cell has been blocked once
(issue #106).  This file covers only the explicit Aquarium integration:
the policy itself is tested in ``test_cell_policy_v3_escape.py``.

No journal written by another motor mode may be reinterpreted as escape,
and the committed frames must stay deterministic across restart.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError, V3_ESCAPE_MODE, V3_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.cell_policy_v3_escape import (
    MAX_CONSECUTIVE_RESTS,
    SCHEMA as ESCAPE_SCHEMA,
    LEARNING_SCHEMA as ESCAPE_LEARNING_SCHEMA,
    CellExplorerV3Escape,
)


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class EscapeModeAquariumTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)

    def open(self, name, mode=V3_ESCAPE_MODE):
        return Aquarium(
            self.dir / name, SEED, GRAPH, agent_id=SEED["genome_id"],
            motor_mode=mode, neural_mode="disabled",
        )

    def test_fresh_journal_pins_the_strict_escape_contract(self):
        with self.open("escape.sqlite3") as aquarium:
            for _ in range(40):
                aquarium.step()
            checkpoint = aquarium.checkpoint()
            self.assertEqual(aquarium.stored_events, 41)
            self.assertEqual(
                aquarium.manifest["developmental_schema"], ESCAPE_SCHEMA,
            )
            self.assertEqual(
                aquarium.manifest["developmental_policy"], V3_ESCAPE_MODE,
            )
            self.assertEqual(
                aquarium.manifest["max_consecutive_rests"],
                MAX_CONSECUTIVE_RESTS,
            )
            state = checkpoint["developmental_state"]
            self.assertEqual(state["schema"], ESCAPE_SCHEMA)
            self.assertEqual(state["updates"], 40)
            self.assertEqual(state["last_tick"], 39)
            self.assertEqual(state["evictions"], 0)
            self.assertEqual(
                CellExplorerV3Escape.from_snapshot(state).snapshot(), state,
            )
            self.assertIsInstance(aquarium.motor, CellExplorerV3Escape)

        wire = inspect_journal(self.dir / "escape.sqlite3")
        self.assertEqual(wire["format"], "local_cell_v3_escape")
        self.assertEqual(wire["state_schema"], ESCAPE_SCHEMA)
        self.assertEqual(wire["frames"], 40)
        self.assertEqual(wire["events"], 41)

    def test_restart_replays_committed_frames_without_divergence(self):
        frames = []
        with self.open("first.sqlite3") as aquarium:
            for _ in range(25):
                frames.append(aquarium.step())
            first = aquarium.checkpoint()

        with self.open("first.sqlite3") as reopened:
            self.assertEqual(reopened.tick, 25)
            self.assertEqual(reopened.checkpoint()["developmental_state"],
                             first["developmental_state"])
            for _ in range(15):
                frames.append(reopened.step())
            resumed = reopened.checkpoint()
        self.assertEqual(resumed["tick"], 40)
        self.assertEqual(resumed["developmental_state"]["updates"], 40)

        # A second, independent journal must commit byte-equivalent frames.
        with self.open("second.sqlite3") as independent:
            for expected in frames:
                self.assertEqual(independent.step(), expected)
            self.assertEqual(independent.checkpoint()["developmental_state"],
                             resumed["developmental_state"])

    def test_committed_frames_cannot_exceed_the_rest_budget(self):
        streak = 0
        with self.open("bound.sqlite3") as aquarium:
            for _ in range(300):
                frame = aquarium.step()
                action = frame["developmental"]["intent"]["action"]
                if action == "rest":
                    streak += 1
                    self.assertLessEqual(streak, MAX_CONSECUTIVE_RESTS)
                else:
                    streak = 0
                learning = frame["developmental"]["learning"]
                self.assertEqual(learning["schema"], ESCAPE_LEARNING_SCHEMA)
                self.assertLessEqual(
                    learning["consecutive_rests"], MAX_CONSECUTIVE_RESTS,
                )

    def test_escape_and_plain_v3_journals_refuse_each_other(self):
        with self.open("escape.sqlite3") as aquarium:
            for _ in range(5):
                aquarium.step()

        with self.assertRaises(AquariumError) as caught:
            self.open("escape.sqlite3", V3_MODE)
        self.assertIn("mismatch", str(caught.exception))
        wire = inspect_journal(self.dir / "escape.sqlite3")
        self.assertEqual(wire["format"], "local_cell_v3_escape")
        self.assertEqual(wire["events"], 6)

        with self.open("v3.sqlite3", V3_MODE) as plain:
            for _ in range(5):
                plain.step()

        with self.assertRaises(AquariumError) as caught:
            self.open("v3.sqlite3", V3_ESCAPE_MODE)
        self.assertIn("mismatch", str(caught.exception))
        wire = inspect_journal(self.dir / "v3.sqlite3")
        self.assertEqual(wire["format"], V3_MODE)
        self.assertEqual(wire["events"], 6)

    def test_cli_selects_the_escape_mode_explicitly(self):
        db = self.dir / "cli.sqlite3"
        result = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
             "--ticks", "5", "--motor-mode", V3_ESCAPE_MODE,
             "--neural-mode", "disabled"],
            cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        checkpoint = json.loads(result.stdout)
        self.assertEqual(checkpoint["tick"], 5)
        self.assertEqual(checkpoint["stored_events"], 6)
        self.assertEqual(checkpoint["developmental_state"]["schema"],
                         ESCAPE_SCHEMA)
        self.assertEqual(checkpoint["developmental_state"]["updates"], 5)
        self.assertIn("INTERNAL_SYNTHETIC_MOTOR_PLAY_NOT_REAL_ACTUATION",
                      checkpoint["status"])

    def test_escape_mode_rejects_foreign_room_and_scripted_movement(self):
        with self.assertRaises(AquariumError):
            Aquarium(self.dir / "wrong-room.sqlite3", SEED, GRAPH,
                     agent_id=SEED["genome_id"], motor_mode=V3_ESCAPE_MODE,
                     room=None, start=(1.5, 2.0), neural_mode="disabled")
        with self.open("movement.sqlite3") as aquarium:
            with self.assertRaises(AquariumError):
                aquarium.step(dx=0.02, dy=0.0)
            self.assertEqual(aquarium.tick, 0)
            self.assertEqual(aquarium.stored_events, 1)


if __name__ == "__main__":
    unittest.main()
