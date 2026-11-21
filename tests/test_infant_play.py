"""R0.7 CPU-only goal-free motor learning and observer-trace tests."""

import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mojum.infant_play import InfantError, InfantPlay, MotorExperience
from mojum.noosphere import NoosphereError
from mojum.world_room import Room, initial_state


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class InfantTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "infant.sqlite3"

    def open(self, **kw):
        return InfantPlay(self.db, GRAPH, **kw)

    def test_first_experiment_is_untrained_and_not_goal_directed(self):
        with self.open() as infant:
            frame = infant.step()
            self.assertEqual(infant.tick, 1)
            self.assertEqual(infant.model.updates, 1)
            self.assertNotIn("goal", frame["experiment"])
            self.assertIsNone(infant.manifest["goal"])
            self.assertEqual(frame["experiment"]["prior_visits"], 0)
            self.assertTrue(frame["viewer"]["experiment"]["novel_attempt"])
            self.assertEqual(frame["viewer"]["inference"]["kind"], "local_empirical_action_effect")
            self.assertFalse(frame["viewer"]["spectator_scene"]["available_to_agent"])
            self.assertEqual(frame["viewer"]["spectator_scene"]["obstacles"],
                             [[2, 1, .1, 2]])
            self.assertEqual(frame["viewer"]["source"],
                             "DETERMINISTIC_SYNTHETIC_TRACE_NOT_CONSCIOUS_EXPERIENCE")
            self.assertIn("single_action_result_not_the_organism",
                          frame["viewer"]["assessment"]["grade_of"])
            self.assertIsNone(frame["experiment"]["motor_command"])
            self.assertEqual(frame["brain_status"], "NOT_A_NEURAL_BRAIN")
            self.assertEqual(frame["physical_model"], "SYNTHETIC_POINT_ROOM_ONLY")

    def test_observer_trace_directly_matches_committed_event(self):
        with self.open() as infant:
            results = [infant.step() for _ in range(25)]
            view = infant.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(len(view), 26)
            for frame, item in zip(results, view[1:]):
                self.assertEqual(frame, item["event"]["payload"])
                self.assertEqual(frame["viewer"]["outcome"]["position"],
                                 frame["world"]["position"])
                self.assertEqual(frame["viewer"]["memory"]["times_tried"],
                                 frame["experience"]["times_tried"])
                self.assertEqual(frame["viewer"]["assessment"]["grade_of"],
                                 "single_action_result_not_the_organism")
                self.assertEqual(item["event"]["visibility"], "PRIVATE")
            self.assertEqual(len(infant.store.dataset()["projection"]["items"]), 0)

    def test_50_tick_reopen_and_exact_model_replay(self):
        with self.open() as infant:
            for _ in range(25):
                infant.step()
            initial = infant.checkpoint()
        with self.open() as infant:
            self.assertEqual(infant.checkpoint(), initial)
            for _ in range(25):
                infant.step()
            final = infant.checkpoint()
        with self.open() as infant:
            self.assertEqual(infant.checkpoint(), final)
            self.assertEqual(infant.model.updates, 50)

    def test_two_independent_runs_identical(self):
        alternate = self.db.with_name("other.sqlite3")
        with self.open() as left, InfantPlay(alternate, GRAPH) as right:
            for _ in range(100):
                self.assertEqual(left.step(), right.step())
            self.assertEqual(left.checkpoint(), right.checkpoint())

    def test_loss_of_cas_never_mutates_local_motor_memory(self):
        with self.open() as a, self.open() as b:
            old_model = b.model.snapshot()
            old_world, old_tick = b.world, b.tick
            a.step()
            with self.assertRaises(NoosphereError):
                b.step()
            self.assertEqual(b.model.snapshot(), old_model)
            self.assertEqual(b.world, old_world)
            self.assertEqual(b.tick, old_tick)

    def test_valid_motor_babbling_does_not_cross_world_only_gate(self):
        with self.open() as infant:
            for _ in range(100):
                payload = infant.step()
                p = payload["experiment"]
                self.assertIn(p["action"], ("rest", "x+", "x-", "y+", "y-"))
                self.assertLessEqual(abs(p["dx"]) + abs(p["dy"]), .040000001)
                self.assertIsNone(p["motor_command"])
                self.assertEqual(p["capability"], "SYNTHETIC_POINT_ROOM_ONLY")
                rating = payload["viewer"]["assessment"]["score_0_to_5"]
                if p["action"] == "rest":
                    self.assertIsNone(rating)
                else:
                    self.assertGreaterEqual(rating, 0)
                    self.assertLessEqual(rating, 5)

    def test_blocked_motion_creates_observable_failure_evidence(self):
        wall = Room(4, 4, ((1.04, 1.0, .1, 2),))
        with self.open(room=wall, start=(1, 2)) as infant:
            saw = False
            for _ in range(250):
                frame = infant.step()
                if frame["world"]["blocked"]:
                    self.assertEqual(frame["viewer"]["outcome"]["actual_effect"], 0.0)
                    self.assertEqual(frame["viewer"]["inference"]["text"],
                                     "this_action_did_not_move_me")
                    self.assertEqual(frame["viewer"]["assessment"]["score_0_to_5"], 0.0)
                    saw = True
                    break
            self.assertTrue(saw)

    def test_corrupt_journal_cannot_reopen(self):
        with self.open() as infant:
            infant.step()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET topic='tampered' WHERE seq=2")
        with self.assertRaises(NoosphereError):
            self.open()

    def test_mismatched_world_or_seed_requires_new_journal(self):
        with self.open() as infant:
            infant.step()
        with self.assertRaises(InfantError):
            self.open(seed=999)
        with self.assertRaises(InfantError):
            self.open(room=Room(3, 3))

    def test_invalid_and_forged_motor_memory_fail_closed(self):
        motor = MotorExperience()
        baseline = motor.snapshot()
        for edited in (
            dict(baseline, updates=1),
            dict(baseline, rng=True),
            dict(baseline, seed=False),
            dict(baseline, cells={"0:0": {}}),
            dict(baseline, cells={"a:b": {}}),
            dict(baseline, cells={"0:0": {"rest": {"visits": 1,
                                                       "mean_effect": float("nan")}}}),
        ):
            with self.subTest(state=str(edited)[:70]):
                with self.assertRaises(InfantError):
                    MotorExperience.from_snapshot(edited)
        with self.assertRaises(InfantError):
            MotorExperience(seed=True)
        self.assertEqual(motor.snapshot(), baseline)

    def test_spatial_cell_keys_fail_closed_outside_memory_budget(self):
        motor = MotorExperience()
        for x, y in (
            (-1e-9, 0.0), (0.0, -0.5), (-0.49, 1.0),
            (5000.0, 0.0), (0.0, 10000.0),
            (1e308, 0.0), (0.0, -1e308),
            (float("inf"), 0.0), (0.0, float("nan")),
        ):
            with self.subTest(x=x, y=y):
                with self.assertRaises(InfantError):
                    motor.propose(SimpleNamespace(x=x, y=y), False)
        # Rejected positions must not consume a cell or advance exploration.
        self.assertEqual(motor.cells, {})
        self.assertEqual(motor.updates, 0)
        self.assertEqual(motor.rng, motor.seed)

    def test_spatial_cell_keys_use_half_open_floor_boundaries(self):
        motor = MotorExperience()
        self.assertEqual(
            motor.propose(SimpleNamespace(x=0.5, y=0.0), False)["cell"], "1:0")
        self.assertEqual(
            motor.propose(SimpleNamespace(x=0.4999, y=0.25), False)["cell"], "0:0")
        self.assertEqual(
            motor.propose(SimpleNamespace(x=4999.5, y=4999.9), False)["cell"],
            "9999:9999")

    def test_forged_motor_action_never_commits(self):
        original = MotorExperience.propose
        with self.open() as infant:
            before = infant.checkpoint()
            for edit in (
                {"motor_command": {"joint": "real", "target": 1}},
                {"capability": "HARDWARE"},
                {"dx": 50.0},
                {"action": "robot.arm"},
            ):
                def tamper(motor, *args, **kwargs):
                    proposal = original(motor, *args, **kwargs)
                    proposal.update(edit)
                    return proposal
                with patch.object(MotorExperience, "propose", tamper):
                    with self.subTest(edit=edit):
                        with self.assertRaises(InfantError):
                            infant.step()
                self.assertEqual(infant.checkpoint(), before)
                self.assertEqual(infant.store.count(), 1)

    def test_cli_private_watch_jsonl_and_restart(self):
        cmd = [
            sys.executable, "-m", "mojum.infant_play",
            "--db", str(self.db), "--ticks", "8", "--watch",
        ]
        a = [json.loads(line) for line in subprocess.check_output(cmd, text=True).splitlines()]
        b = [json.loads(line) for line in subprocess.check_output(cmd, text=True).splitlines()]
        self.assertEqual(len(a), 9)
        self.assertEqual(len(b), 9)
        self.assertEqual(a[0]["tick"], 0)
        self.assertEqual(b[0]["tick"], 8)
        self.assertIn("checkpoint", a[-1])
        self.assertEqual(a[-1]["checkpoint"]["tick"], 8)
        self.assertEqual(b[-1]["checkpoint"]["tick"], 16)
        self.assertEqual(b[-1]["events"], 17)

    def test_offline_browser_observer_is_file_only_without_remote_dependencies(self):
        page = (ROOT / "examples/infant_observer.html").read_text(encoding="utf-8")
        self.assertIn('mojum.observation.trace/1', page)
        self.assertIn('id="scene"', page)
        self.assertIn('id="history"', page)
        self.assertIn('spectator_scene', page)
        self.assertIn('file.text()', page)
        self.assertNotIn('fetch(', page)
        self.assertNotIn('https://', page)
        self.assertNotIn('<script src=', page)

    def test_no_network_or_public_viewer_server_by_default(self):
        with self.open() as infant:
            infant.step()
            self.assertEqual(infant.manifest["provenance"],
                             "synthetic-fixture-no-real-world-observation")
            self.assertTrue(all(
                item["event"]["visibility"] == "PRIVATE"
                for item in infant.store.dataset(include_nonpublic=True)["projection"]["items"]
            ))


if __name__ == "__main__":
    unittest.main()
