"""R0.13 opt-in developmental Aquarium and deterministic crash/replay QA."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError
from mojum.developmental import MotorPlay, DevelopmentalError, MOVES, SCHEMA
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text())
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text())


class MotorPlayUnitTests(unittest.TestCase):
    def test_measured_outcomes_determine_choice_without_target(self):
        blocked_east, blocked_north = MotorPlay(), MotorPlay()
        for tick, (name, _, _) in enumerate(MOVES):
            self.assertEqual(blocked_east.choose(tick)["kind"], name)
            self.assertEqual(blocked_north.choose(tick)["kind"], name)
            blocked_east.observe(tick=tick, kind=name, blocked=(name == "east"))
            blocked_north.observe(tick=tick, kind=name, blocked=(name == "north"))
        self.assertEqual(blocked_east.choose(4)["kind"], "north")
        self.assertEqual(blocked_north.choose(4)["kind"], "east")

    def test_malformed_or_stale_motor_history_fails_closed(self):
        policy = MotorPlay()
        for tick in (True, -1, 1, "0"):
            with self.assertRaises(DevelopmentalError):
                policy.choose(tick)
        for kind, blocked in (("east", 1), ({}, False), ("oracle", False)):
            with self.assertRaises(DevelopmentalError):
                policy.observe(tick=0, kind=kind, blocked=blocked)
        self.assertEqual(policy.snapshot()["observations"], 0)
        policy.observe(tick=0, kind="east", blocked=False)
        state = policy.snapshot()
        state["samples"]["east"]["blocked"] = 2
        with self.assertRaises(DevelopmentalError):
            MotorPlay.from_snapshot(state)

    def test_boundary_has_no_geometry_target_or_future_observation(self):
        policy = MotorPlay()
        self.assertNotIn("room", policy.snapshot())
        self.assertNotIn("target", policy.snapshot())
        intent = policy.choose(0)
        self.assertEqual(set(intent), {
            "schema", "kind", "dx", "dy", "expected_unblocked_probability",
            "prior_trials", "interface",
        })
        self.assertEqual(intent["schema"], SCHEMA)
        self.assertLessEqual(abs(intent["dx"]) + abs(intent["dy"]), 0.125)


class AquariumMotorPlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "motor.sqlite3"

    def aquarium(self, **kwargs):
        return Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture", **kwargs)

    def test_one_private_journal_and_exact_restart(self):
        with self.aquarium() as q:
            frames = [q.step() for _ in range(17)]
            self.assertEqual([f["developmental_controller"]["intent"]["kind"]
                              for f in frames[:4]], [m[0] for m in MOVES])
            self.assertEqual(frames[10]["developmental_controller"]["intent"]["kind"], "rest")
            self.assertTrue(all(f["brain"]["action"] is None for f in frames))
            self.assertTrue(all(not f["developmental_controller"]["real_actuation"]
                                for f in frames))
            self.assertEqual(q.store.count(), 18)
            self.assertEqual(len(q.store.dataset()["projection"]["items"]), 0)
            saved = q.checkpoint()
        with self.aquarium() as q:
            self.assertEqual(q.checkpoint(), saved)
            q.step()
            self.assertEqual(q.tick, 18)
        with self.aquarium() as q:
            self.assertEqual(q.tick, 18)
            self.assertEqual(q.motor.observations, 18)

    def test_different_measured_experiences_cause_different_actions(self):
        room_east_blocked = Room(4, 4, ((1.98, 1.5, .1, 1.0),))
        room_north_blocked = Room(4, 4, ((1.5, 2.02, 1.0, .1),))
        db2 = Path(self.tmp.name) / "second.sqlite3"
        with self.aquarium(room=room_east_blocked, start=(1.9, 2)) as a, \
             Aquarium(db2, SEED, GRAPH, motor_mode="developmental_fixture",
                      room=room_north_blocked, start=(1.9, 2)) as b:
            for _ in range(4):
                a.step()
                b.step()
            self.assertTrue(a.motor.samples["east"]["blocked"])
            self.assertTrue(b.motor.samples["north"]["blocked"])
            self.assertEqual(a.motor.choose(4)["kind"], "north")
            self.assertEqual(b.motor.choose(4)["kind"], "east")
            self.assertNotEqual(a.step()["movement_request"], b.step()["movement_request"])

    def test_failed_append_preserves_entire_controller_world_and_tick(self):
        with self.aquarium() as q:
            q.step()
            before = q.checkpoint()
            with patch.object(q, "_append_record", side_effect=RuntimeError("simulated failure")):
                with self.assertRaises(RuntimeError):
                    q.step()
            self.assertEqual(q.checkpoint(), before)
            self.assertEqual(q.store.count(), 2)

    def test_stale_developmental_writer_fails_without_state_adoption(self):
        with self.aquarium() as writer, self.aquarium() as stale:
            old = stale.checkpoint()
            writer.step()
            with self.assertRaisesRegex(NoosphereError, "stale event head"):
                stale.step()
            self.assertEqual(stale.tick, 0)
            self.assertEqual(stale.world.revision, old["world_revision"])
            self.assertEqual(stale.motor.snapshot(), old["motor_state"])
            self.assertEqual(stale.learner.snapshot(), old["neural_state"])
            with self.assertRaises(AquariumError):
                stale.checkpoint()
        with self.aquarium() as resumed:
            self.assertEqual(resumed.tick, 1)
            self.assertEqual(resumed.motor.observations, 1)
            self.assertEqual(resumed.store.count(), 2)

    def test_external_action_refused_and_mode_pinned_to_bootstrap(self):
        with self.aquarium() as q:
            before = q.checkpoint()
            with self.assertRaises(AquariumError):
                q.step(dx=0)
            with self.assertRaises(AquariumError):
                q.step(dy=0)
            self.assertEqual(q.checkpoint(), before)
            q.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, motor_mode="scripted")
        with self.assertRaises(AquariumError):
            self.aquarium(neural_mode="disabled")

    def test_legacy_mode_retains_frame_shape_and_bootstrap(self):
        other = Path(self.tmp.name) / "legacy.sqlite3"
        with Aquarium(other, SEED, GRAPH) as q:
            frame = q.step()
            self.assertNotIn("developmental_controller", frame)
            self.assertNotIn("motor_mode", q.manifest)
            self.assertNotIn("motor_state", q.checkpoint())


if __name__ == "__main__":
    unittest.main()
