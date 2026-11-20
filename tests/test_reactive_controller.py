"""R0.4 reactive *synthetic* closed-loop integration / independent QA targets."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError
from mojum.reactive_controller import ControllerError, checked_goal, decide
from mojum.world_room import Room, initial_state


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class ReactiveControllerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "reactive.sqlite3"

    def open(self, **kwargs):
        return Aquarium(self.db, SEED, GRAPH, control_mode="reactive_fixture", **kwargs)

    def test_autonomous_frame_depends_on_current_synthetic_heartbeat(self):
        with self.open() as aquarium:
            f = aquarium.step()
            self.assertEqual(f["controller"]["schema"], "mojum.controller.reactive-point-room/1")
            self.assertEqual(f["controller"]["observed_at_ns"], f["interoception"]["observed_at_ns"])
            self.assertEqual(f["controller"]["world_revision"], initial_state(aquarium.room, *aquarium.start).revision)
            self.assertEqual(f["movement_request"]["dx"], f["controller"]["dx"])
            self.assertEqual(f["movement_request"]["dy"], 0.0)
            self.assertIn(abs(f["controller"]["dx"]), (0.02, 0.04))
            self.assertIsNone(f["controller"]["motor_command"])
            self.assertEqual(f["controller"]["actuation"], "SYNTHETIC_POINT_ROOM_ONLY")
            self.assertIsNone(f["brain"]["action"])
            self.assertEqual(f["brain"]["status"], "UNIMPLEMENTED")
            self.assertEqual(f["physical_model"], "NONE_FIXTURE_ONLY")

    def test_committed_blockage_changes_next_policy_action(self):
        with self.open() as aquarium:
            for _ in range(300):
                frame = aquarium.step()
                if frame["world"]["blocked"] and frame["controller"]["axis"] == "x":
                    after = aquarium.step()
                    self.assertEqual(after["controller"]["axis"], "y")
                    self.assertGreater(after["controller"]["dy"], 0)
                    self.assertTrue(after["controller"]["prior_blocked"])
                    break
            else:
                self.fail("expected initial synthetic x-axis obstacle collision")

    def test_can_pass_fixture_obstacle_with_reactive_feedback(self):
        with self.open() as aquarium:
            for _ in range(350):
                aquarium.step()
            self.assertGreater(aquarium.world.x, 2.1)
            self.assertGreater(aquarium.world.y, 3.0)
            self.assertLessEqual(aquarium.world.x, aquarium.room.width)

    def test_resume_matches_independent_uninterrupted_run(self):
        other_path = self.db.with_name("uninterrupted.sqlite3")
        with self.open() as aquarium:
            for _ in range(61):
                aquarium.step()
            before_restart = aquarium.checkpoint()
        with self.open() as aquarium:
            self.assertEqual(aquarium.checkpoint(), before_restart)
            for _ in range(100):
                aquarium.step()
            resumed = aquarium.checkpoint()
        with Aquarium(other_path, SEED, GRAPH, control_mode="reactive_fixture") as other:
            for _ in range(161):
                other.step()
            self.assertEqual(resumed, other.checkpoint())
        with self.open() as restored:
            self.assertEqual(restored.checkpoint(), resumed)

    def test_reactive_mode_rejects_external_overrides_without_commit(self):
        with self.open() as aquarium:
            before = aquarium.checkpoint()
            for options in ({"dx": 0}, {"dy": 0}, {"dx": .02, "dy": 0}):
                with self.assertRaises(AquariumError):
                    aquarium.step(**options)
            self.assertEqual(before, aquarium.checkpoint())
            self.assertEqual(aquarium.store.count(), 1)

    def test_stale_sqlite_head_does_not_mutate_controller_feedback(self):
        with self.open() as a, self.open() as b:
            before_head = b.store.head()
            before_tick = b.tick
            before_world = b.world
            before_feedback = b._last_feedback
            before_neural = b.learner.snapshot()
            a.step()
            with self.assertRaises(NoosphereError):
                b.step()
            self.assertEqual(b.tick, before_tick)
            self.assertEqual(b.world, before_world)
            self.assertEqual(b._last_feedback, before_feedback)
            self.assertEqual(b.learner.snapshot(), before_neural)
            # The shared SQLite head *does* move because another writer committed.
            self.assertNotEqual(b.store.head(), before_head)

    def test_mode_or_goal_switch_fails_closed_on_reopen(self):
        with self.open() as aquarium:
            aquarium.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, control_mode="manual")
        with self.assertRaises(AquariumError):
            self.open(goal=(3.0, 3.0))

    def test_legacy_manual_bootstrap_has_no_new_fields(self):
        manual_path = self.db.with_name("manual.sqlite3")
        with Aquarium(manual_path, SEED, GRAPH) as aquarium:
            frame = aquarium.step()
            self.assertNotIn("controller", frame)
            self.assertNotIn("control_mode", aquarium.manifest)
            self.assertNotIn("control_mode", aquarium.checkpoint())
        with Aquarium(manual_path, SEED, GRAPH) as reopened:
            self.assertEqual(reopened.tick, 1)

    def test_goal_validation_fails_closed(self):
        room = Room(4, 4, ((2, 1, .1, 2),))
        for goal in ((2, 2), (5, 1), (float("nan"), 1), (True, 1), ("3", 1), (1,)):
            with self.assertRaises(ControllerError):
                checked_goal(room, goal)
        with self.assertRaises(AquariumError):
            self.open(goal=(2, 2))

    def test_malformed_previous_feedback_or_sensory_is_rejected(self):
        room = Room(4, 4)
        state = initial_state(room, 1, 1)
        sensory = {"kind": "interoception", "observed_at_ns": 0, "heartbeat": {"pulse": True}}
        for previous in ({"dx": 0.04, "dy": 0.0}, {"dx": 0.04, "dy": 0.0, "blocked": 1}):
            with self.assertRaises(ControllerError):
                decide(room, state, sensory, (3, 3), previous)
        with self.assertRaises(ControllerError):
            decide(room, state, {"kind": "vision"}, (3, 3))

    def test_cli_reactive_resume(self):
        cmd = [
            sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
            "--ticks", "12", "--control-mode", "reactive_fixture",
        ]
        first = json.loads(subprocess.check_output(cmd, text=True))
        second = json.loads(subprocess.check_output(cmd, text=True))
        self.assertEqual(first["tick"], 12)
        self.assertEqual(second["tick"], 24)
        self.assertEqual(second["stored_events"], 25)
        self.assertIn("NOT_LEARNED", second["status"])

    def test_cli_rejects_untrusted_manual_movement_override(self):
        cmd = [
            sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
            "--control-mode", "reactive_fixture", "--dx", "0.3",
        ]
        result = subprocess.run(cmd, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not accepted", result.stderr)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
