"""R0.14 directional sensor QA — mirrors, hidden walls, single-journal replay."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.directional_outcomes import BearingOutcomeMemory, SCHEMA
from mojum.sensory_play import (
    CuriosityError, DIRECTION_SENSE_SCHEMA, Toy, ToyScene,
)
from mojum.temporal_outcomes import TemporalOutcomeError
from mojum.noosphere import NoosphereError
from mojum.world_room import Room, advance, initial_state

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def scene(side, rgb=(180, 20, 90)):
    return ToyScene(toys=(Toy(
        "spectator." + side,
        1.22 if side == "right" else .78,
        2.0, .1, rgb, .5,
    ),))


def observation_pair(toys, room=None):
    room = Room(4, 4) if room is None else room
    state = initial_state(room, 1., 2.)
    before = toys.sense_direction(state, occluding_obstacles=room.obstacles)
    future, result = advance(
        room, state, tick=1, dx=.04, dy=0.,
        expected_revision=state.revision,
    )
    after = toys.sense_direction(future, occluding_obstacles=room.obstacles)
    return before, after, result


class BearingGuidanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "private.sqlite3"
        self.other = Path(temp.name) / "other.sqlite3"

    def baby(self, db=None, **kwargs):
        return Aquarium(
            self.path if db is None else db, SEED, GRAPH,
            neural_mode="disabled", exploration_mode="goal_free",
            sensory_mode="toy_probe", temporal_mode="bearing_guided",
            **kwargs,
        )

    def test_same_numeric_range_but_opposite_bearing(self):
        right, left = observation_pair(scene("right")), observation_pair(
            scene("left", (10, 100, 240)),
        )
        for before, after, result in (right, left):
            self.assertEqual(before["schema"], DIRECTION_SENSE_SCHEMA)
            self.assertEqual(before["range_bucket_10cm"], 2)
            self.assertFalse(result["blocked"])
        self.assertEqual(right[0]["bearing_bin"], "x+")
        self.assertEqual(left[0]["bearing_bin"], "x-")
        self.assertEqual(right[1]["range_bucket_10cm"], 1)
        self.assertEqual(left[1]["range_bucket_10cm"], 2)
        self.assertNotIn("spectator", json.dumps(right[0]))

    def test_two_real_measured_contexts_do_not_cancel_out(self):
        right = observation_pair(scene("right"))
        left = observation_pair(scene("left", (10, 100, 240)))
        learned = BearingOutcomeMemory()
        for before, after, result in (right, left):
            learned.observe(before, "x+", after, result["blocked"],
                            learned.predict(before, "x+"))
        self.assertEqual(learned.snapshot()["updates"], 2)
        self.assertEqual(learned.predict(right[0], "x+"), {
            "observations": 1, "next_bucket": 1, "blocked": False,
        })
        self.assertEqual(learned.predict(left[0], "x+"), {
            "observations": 1, "next_bucket": 2, "blocked": False,
        })
        self.assertEqual(set(learned.snapshot()["models"]), {"x+", "x-"})
        self.assertEqual(BearingOutcomeMemory.from_snapshot(
            learned.snapshot()).snapshot(), learned.snapshot())
        self.assertNotIn("spectator", json.dumps(learned.snapshot()))
        self.assertNotIn("rgb", json.dumps(learned.snapshot()))

    def test_direction_guided_preference_uses_only_matching_bearing(self):
        right = observation_pair(scene("right"))
        learned = BearingOutcomeMemory()
        learned.observe(right[0], "x+", right[1], False,
                        learned.predict(right[0], "x+"))
        with self.baby(room=Room(4, 4), sensory_scene=scene("right")) as child:
            # QA-only injected history: tests native policy semantics, NOT a
            # claim that this training happened in this journal.
            child.temporal_model = learned
            right_guidance, _, right_prefer = child._motor_guidance()
            self.assertIn("x+", right_prefer)
            self.assertEqual(right_guidance["observation"]["bearing_bin"], "x+")
            # Change the fixture for an entirely separate new Aquarium below;
            # never hot-swap a canonical committed scene.
            self.assertEqual(child.store.count(), 1)
        with self.baby(self.other, room=Room(4, 4),
                       sensory_scene=scene("left")) as child:
            child.temporal_model = learned
            left_guidance, _, left_prefer = child._motor_guidance()
            self.assertEqual(left_guidance["observation"]["bearing_bin"], "x-")
            self.assertEqual(left_guidance["predictions"]["x+"]["observations"], 0)
            self.assertNotIn("x+", left_prefer)

    def test_wall_occlusion_masks_direction_and_probe(self):
        wall = Room(4, 4, ((2, 1, .1, 2),))
        toys = ToyScene(toys=(
            Toy("spectator.behind", 2.25, 2., .1, (230, 0, 0), .8),
        ))
        pos = initial_state(wall, 1.98, 2.)
        raw = toys.sense_direction(pos)
        self.assertTrue(raw["visible"])
        masked = toys.sense_direction(pos, occluding_obstacles=wall.obstacles)
        self.assertFalse(masked["visible"])
        self.assertIsNone(masked["bearing_bin"])
        self.assertIsNone(masked["range_bucket_10cm"])
        self.assertIsNone(masked["reflectance_rgb"])
        with self.baby(room=wall, start=(1.98, 2.), sensory_scene=toys) as child:
            frame = child.step()
            self.assertIsNone(frame["temporal_range"]["before"]["bearing_bin"])
            self.assertIsNone(frame["motor_guidance"]["observation"]["bearing_bin"])
            self.assertFalse(frame["toy_contact"]["probe_intent"]["requested"])

    def test_one_journal_cold_restart_and_future_intent(self):
        with self.baby() as child, self.baby(self.other) as control:
            ever_known = False
            for _ in range(36):
                a, b = child.step(), control.step()
                self.assertEqual(a, b)
                self.assertEqual(a["temporal_range"]["schema"], SCHEMA)
                self.assertEqual(a["motor_guidance"]["observation"]["bearing_bin"],
                                 a["temporal_range"]["before"]["bearing_bin"])
                self.assertEqual(
                    a["temporal_range"]["before"]["range_bucket_10cm"],
                    a["toy_contact"]["observation"]["range_bucket_10cm"],
                )
                if any(p["observations"] > 0 for p in
                       a["motor_guidance"]["predictions"].values()):
                    ever_known = True
            previous = child.checkpoint()
            future = control.step()
            self.assertTrue(ever_known)
            self.assertEqual(child.store.count(), 37)
            self.assertEqual(child.temporal_model.updates, 36)
            self.assertEqual(child.manifest["relative_bearing_schema"],
                             DIRECTION_SENSE_SCHEMA)
        with self.baby() as reopened:
            self.assertEqual(reopened.checkpoint(), previous)
            self.assertEqual(reopened.step(), future)
            self.assertEqual(reopened.store.count(), 38)

    def test_stale_writer_rejected_without_bearing_memory_adoption(self):
        with self.baby() as writer, self.baby() as stale:
            snapshot = stale.temporal_model.snapshot()
            writer.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.temporal_model.snapshot(), snapshot)
            with self.assertRaises(AquariumError):
                stale.checkpoint()

    def test_invalid_direction_and_old_mode_never_reinterpret_history(self):
        a, b, _ = observation_pair(scene("right"))
        for candidate in ({"bearing_bin": "north"},
                          {"bearing_bin": None}, {"bearing_bin": []}):
            broken = copy.deepcopy(a)
            broken.update(candidate)
            with self.subTest(value=candidate), self.assertRaises(
                    TemporalOutcomeError):
                BearingOutcomeMemory().predict(broken, "x+")
        invisible = copy.deepcopy(a)
        invisible.update(
            visible=False, bearing_bin=None,
            reflectance_rgb=None, range_bucket_10cm=None,
        )
        self.assertIsNone(BearingOutcomeMemory().predict(
            invisible, "x+")["blocked"])
        malformed = BearingOutcomeMemory().snapshot()
        malformed["models"] = {"x++": {"invalid": True}}
        with self.assertRaises(TemporalOutcomeError):
            BearingOutcomeMemory.from_snapshot(malformed)
        with self.baby() as child:
            child.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.path, SEED, GRAPH, neural_mode="disabled",
                     exploration_mode="goal_free", sensory_mode="toy_probe",
                     temporal_mode="range_guided")
        with self.assertRaises(AquariumError):
            Aquarium(self.path, SEED, GRAPH, neural_mode="disabled",
                     exploration_mode="goal_free", sensory_mode="toy_probe",
                     temporal_mode="none")

    def test_cli_two_run_and_invalid_mode_before_sqlite_birth(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        cmd = [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.path), "--ticks", "2",
            "--exploration-mode", "goal_free",
            "--sensory-mode", "toy_probe",
            "--temporal-mode", "bearing_guided",
            "--neural-mode", "disabled",
        ]
        first = json.loads(subprocess.check_output(
            cmd, env=env, cwd=ROOT, text=True, timeout=30,
        ))
        next_run = json.loads(subprocess.check_output(
            cmd, env=env, cwd=ROOT, text=True, timeout=30,
        ))
        self.assertEqual(first["temporal_range_state"]["updates"], 2)
        self.assertEqual(next_run["temporal_range_state"]["updates"], 4)
        self.assertEqual(next_run["stored_events"], 5)
        rejected_path = self.path.with_name("must-not-exist.sqlite3")
        bad = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db",
             str(rejected_path), "--ticks", "1",
             "--temporal-mode", "bearing_guided"],
            cwd=ROOT, env=env, text=True, capture_output=True,
            timeout=30, check=False,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertFalse(rejected_path.exists())


if __name__ == "__main__":
    unittest.main()
