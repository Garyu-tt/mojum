"""R0.12: moving synthetic toy truth exists inside the real fixture loop.

These assertions do not infer visual recognition, real rigid-body physics
or consciousness. All true toy IDs/trajectories are spectator-only.
"""

import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.moving_toys import (
    MovingToyScene, MOTION_SCHEMA, MOTION_SOURCE,
    MAX_SPEED_M_PER_TICK, MAX_SIM_TICKS,
)
from mojum.noosphere import NoosphereError
from mojum.sensory_play import CuriosityError, SensoryPlay, Toy, ToyScene
from mojum.world_room import advance, initial_state


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def at_tick(scene, x, y, tick):
    state = initial_state(scene.room, x, y)
    for i in range(1, tick + 1):
        state, _ = advance(
            scene.room, state, tick=i, dx=0, dy=0,
            expected_revision=state.revision,
        )
    return state


class DynamicSyntheticSceneQA(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "moving-private.sqlite3"

    def test_pure_deterministic_kinematic_motion_preserves_initial_layout(self):
        scene = MovingToyScene()
        original = scene.manifest()
        self.assertEqual(original["kinematic_schema"], MOTION_SCHEMA)
        self.assertEqual(original["kinematic_source"], MOTION_SOURCE)
        first = scene.at(0)
        self.assertEqual(first, scene.toys)
        self.assertEqual(first, scene.at(0))
        self.assertEqual(scene.at(1), scene.at(1))
        self.assertNotEqual(scene.at(10), first)
        self.assertEqual(scene.manifest(), original)
        self.assertEqual(scene.toys, first)
        scene.spectator_at(21)
        self.assertEqual(scene.at(10), scene.at(10))

    def test_true_toy_motion_is_bounded_per_tick_and_reflects_at_walls(self):
        scene = MovingToyScene(toys=(
            Toy("hidden-moving", 3.8, 1.0, .1, (100, 60, 50), .4),
        ), velocities=((.049, -.009),))
        self.assertEqual(len(scene.at(200)), 1)
        for tick in range(150):
            current, next_ = scene.at(tick)[0], scene.at(tick + 1)[0]
            self.assertGreaterEqual(current.x, current.radius_m)
            self.assertLessEqual(current.x, scene.room.width - current.radius_m)
            self.assertGreaterEqual(current.y, current.radius_m)
            self.assertLessEqual(current.y, scene.room.height - current.radius_m)
            distance = math.hypot(next_.x - current.x, next_.y - current.y)
            self.assertLessEqual(distance, MAX_SPEED_M_PER_TICK + 1e-9)
        self.assertLess(scene.at(20)[0].x, scene.at(0)[0].x)
        with self.assertRaises(CuriosityError):
            scene.at(MAX_SIM_TICKS + 1)

    def test_two_identical_color_objects_physically_swap_world_positions(self):
        color = (95, 55, 100)
        scene = MovingToyScene(
            toys=(
                Toy("hidden.a", 1.2, 2, .14, color, .1),
                Toy("hidden.b", 2.8, 2, .14, color, .9),
            ),
            velocities=((.04, 0), (-.04, 0)),
        )
        initial, moved = at_tick(scene, 1.0, 2.0, 0), at_tick(scene, 1.0, 2.0, 40)
        self.assertAlmostEqual(scene.at(40)[0].x, 2.8)
        self.assertAlmostEqual(scene.at(40)[1].x, 1.2)
        start_cue, later_cue = scene.sense(initial), scene.sense(moved)
        self.assertEqual(start_cue["reflectance_rgb"], later_cue["reflectance_rgb"])
        # Range quantization can differ by one bucket at a floating
        # boundary even when the physical distances are nearly equal.
        self.assertLessEqual(abs(start_cue["range_bucket_10cm"]
                                 - later_cue["range_bucket_10cm"]), 1)
        self.assertNotIn("id", start_cue)
        self.assertNotIn("softness", later_cue)
        self.assertEqual(scene.probe(initial, start_cue)["measured_deflection"], .1)
        self.assertEqual(scene.probe(moved, later_cue)["measured_deflection"], .9)
        self.assertNotEqual(scene.spectator_at(0), scene.spectator_at(40))

    def test_sensors_are_numeric_not_privileged_spectator_oracle(self):
        scene = MovingToyScene()
        for tick in (0, 5, 10, 23, 47):
            state = at_tick(scene, 1.0, 2.0, tick)
            cue = scene.sense(state)
            bearing = scene.directional_cue(state, cue)
            self.assertNotIn("id", cue)
            self.assertNotIn("softness", cue)
            self.assertNotIn("velocity", cue)
            self.assertNotIn("id", bearing)
            self.assertNotIn("velocity", bearing)
            spectator = scene.spectator_at(tick)
            self.assertTrue(spectator["spectator_only_toy_identity_and_softness"])
            self.assertEqual(spectator["truth_at_tick"], tick)
            self.assertEqual(spectator["kinematic_schema"], MOTION_SCHEMA)
            self.assertTrue(all("id" in t and "softness" in t
                                for t in spectator["toys"]))

    def test_invalid_velocity_shapes_out_of_budget_and_no_motion_fail(self):
        toy = (Toy("toy", 1, 2, .14, (20, 30, 40), .2),)
        invalid = (
            ((.1, 0),), ((float("nan"), 0),), ((float("inf"), 0),),
            ((True, 0),), ((0, "one"),), ((0, 0, 0),),
            ((0, 0),), (),
        )
        for velocities in invalid:
            with self.subTest(velocities=velocities):
                with self.assertRaises(CuriosityError):
                    MovingToyScene(toys=toy, velocities=velocities)
        with self.assertRaises(CuriosityError):
            MovingToyScene(toys=toy, velocities=[[.02, 0]])
        with self.assertRaises(CuriosityError):
            MovingToyScene(toys=toy, velocities=((.0001, 0), (.02, 0)))

    def test_full_private_journal_is_replayable_and_kinematics_pinned(self):
        scene = MovingToyScene()
        with SensoryPlay(
            self.db, GRAPH, scene=scene, object_tracking=True,
            contact_guard=True, rgb_tolerance=1,
        ) as baby:
            records = [baby.step() for _ in range(85)]
            last = baby.checkpoint()
            self.assertEqual(baby.store.count(), 86)
            self.assertEqual(baby.store.dataset()["projection"]["items"], [])
            self.assertEqual(
                baby.store.dataset(include_nonpublic=True, limit=86)
                ["projection"]["items"][-1]["event"]["payload"],
                records[-1],
            )
            self.assertEqual(
                baby.manifest["scene"]["kinematic_schema"], MOTION_SCHEMA,
            )
            self.assertEqual(
                records[7]["spectator_scene"], scene.spectator_at(7),
            )
            self.assertEqual(
                records[7]["sensor"]["source"],
                "synthetic_local_proximity_not_camera",
            )
            self.assertEqual(
                records[7]["inference_scope"],
                "observed_numeric_cue_contact_association_only",
            )
            self.assertEqual(records[7]["brain_status"], "NOT_A_NEURAL_BRAIN")
            self.assertFalse(records[7]["object_hypothesis"]["identity_verified"])
            self.assertIsNone(records[7]["experiment"]["motor_command"])
        with SensoryPlay(
            self.db, GRAPH, scene=scene, object_tracking=True,
            contact_guard=True, rgb_tolerance=1,
        ) as baby:
            self.assertEqual(last, baby.checkpoint())
            baby.step()
            new = baby.checkpoint()
        with SensoryPlay(
            self.db, GRAPH, scene=scene, object_tracking=True,
            contact_guard=True, rgb_tolerance=1,
        ) as baby:
            self.assertEqual(new, baby.checkpoint())

    def test_world_motion_is_separate_from_agent_proprioception(self):
        scene = MovingToyScene()
        with SensoryPlay(self.db, GRAPH, scene=scene,
                         object_tracking=True) as baby:
            first = baby.step()
            second = baby.step()
            self.assertNotEqual(first["spectator_scene"]["toys"],
                                second["spectator_scene"]["toys"])
            self.assertNotIn("toys", first["sensor"])
            self.assertNotIn("velocities_m_per_tick", first["sensor"])
            self.assertNotIn("kinematic_source", first["sensor"])

    def test_incompatible_motion_configuration_fails_before_reinterpretation(self):
        scene = MovingToyScene()
        with SensoryPlay(self.db, GRAPH, scene=scene) as child:
            for _ in range(8):
                child.step()
        changed = MovingToyScene(velocities=((.023, 0.0), (-.015, .008)))
        with self.assertRaises(CuriosityError):
            SensoryPlay(self.db, GRAPH, scene=changed)
        with self.assertRaises(CuriosityError):
            SensoryPlay(self.db, GRAPH, scene=ToyScene())
        with SensoryPlay(self.db, GRAPH, scene=scene) as child:
            self.assertEqual(child.tick, 8)

    def test_concurrent_cas_rejection_preserves_observer_world_and_local_memory(self):
        scene = MovingToyScene()
        with SensoryPlay(self.db, GRAPH, scene=scene,
                         object_tracking=True) as left, SensoryPlay(
            self.db, GRAPH, scene=scene, object_tracking=True,
        ) as right:
            saved = right.checkpoint()
            left.step()
            with self.assertRaises(NoosphereError):
                right.step()
            actual = right.checkpoint()
            for field in ("tick", "world_revision", "rng",
                          "object_memory", "association_model"):
                self.assertEqual(saved[field], actual[field])

    def test_cli_moving_world_is_real_per_tick_and_replayable(self):
        args = [
            sys.executable, "-m", "mojum.sensory_play",
            "--db", str(self.db), "--ticks", "8", "--watch",
            "--moving-toys", "--object-tracking", "--contact-guard",
            "--rgb-tolerance", "1",
        ]
        first = [json.loads(line) for line in
                 subprocess.check_output(args, text=True).splitlines()]
        second = [json.loads(line) for line in
                  subprocess.check_output(args, text=True).splitlines()]
        self.assertEqual([f["tick"] for f in first[:-1]], list(range(8)))
        self.assertEqual([f["tick"] for f in second[:-1]], list(range(8, 16)))
        self.assertNotEqual(
            first[0]["spectator_scene"]["toys"],
            second[0]["spectator_scene"]["toys"],
        )
        self.assertEqual(
            second[-1]["checkpoint"]["tick"], 16,
        )
        self.assertEqual(second[-1]["private_events"], 17)
        self.assertEqual(
            second[0]["spectator_scene"]["kinematic_source"], MOTION_SOURCE,
        )
        self.assertNotIn("toys", second[0]["sensor"])
        self.assertNotIn("velocity", second[0]["sensor"])

    def test_cli_rejects_reinterpretation_as_static_without_moving_flag(self):
        moving_args = [
            sys.executable, "-m", "mojum.sensory_play",
            "--db", str(self.db), "--ticks", "3", "--moving-toys",
        ]
        subprocess.check_output(moving_args, text=True)
        plain_args = [
            sys.executable, "-m", "mojum.sensory_play",
            "--db", str(self.db), "--ticks", "1",
        ]
        wrong = subprocess.run(
            plain_args, text=True, capture_output=True,
            timeout=15,
        )
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("incompatible", wrong.stderr)
        with SensoryPlay(
            self.db, GRAPH, scene=MovingToyScene(),
        ) as child:
            self.assertEqual(child.tick, 3)

    def test_r09_static_scene_checkpoint_and_spectator_remain_unchanged(self):
        scene = ToyScene()
        with SensoryPlay(self.db, GRAPH, scene=scene) as child:
            frame = child.step()
            self.assertEqual(frame["spectator_scene"], scene.manifest())
            self.assertNotIn("kinematic_schema", frame["spectator_scene"])
            self.assertNotIn("object_hypothesis", frame)
        with SensoryPlay(self.db, GRAPH, scene=scene) as child:
            self.assertEqual(child.tick, 1)


if __name__ == "__main__":
    unittest.main()
