"""R0.13 slice B: real numeric probe evidence in the canonical SQLite Aquarium."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError
from mojum.sensory_bridge import (
    SensoryBridgeError, PROBE_CAPABILITY, SCHEMA as CONTACT_SCHEMA,
)
from mojum.sensory_play import ContactAssociations, CuriosityError, Toy, ToyScene
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class CanonicalToyContactIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "one-organism.sqlite3"
        self.second = Path(self.tmp.name) / "second.sqlite3"

    def aquarium(self, database=None, **kwargs):
        return Aquarium(database or self.db, SEED, GRAPH,
                        motor_mode="developmental_fixture", sensory_mode="toy_probe",
                        **kwargs)

    def test_birth_is_empty_and_numeric_probe_commits_measured_learning(self):
        with self.aquarium() as a:
            self.assertEqual(a.contact_model.updates, 0)
            self.assertEqual(a.contact_model.table, {})
            frame = a.step()
            contact = frame["toy_contact"]
            self.assertEqual(contact["schema"], CONTACT_SCHEMA)
            self.assertTrue(contact["observation"]["visible"])
            self.assertIsNone(contact["prediction_before"]["predicted_deflection"])
            self.assertTrue(contact["probe_intent"]["requested"])
            self.assertEqual(contact["probe_intent"]["capability"], PROBE_CAPABILITY)
            self.assertIsNone(contact["probe_intent"]["external_actuator"])
            self.assertTrue(contact["measured_contact"]["contact"])
            self.assertEqual(contact["measured_contact"]["measured_deflection"], 0.75)
            self.assertEqual(contact["learning"]["new_prediction"], 0.75)
            self.assertEqual(contact["learning"]["updates"], 1)
            self.assertEqual(a.contact_model.updates, 1)
            self.assertEqual(a.motor.observations, 1)
            self.assertEqual(a.store.count(), 2)
            self.assertEqual(a.store.dataset()["projection"]["items"], [])
            private = a.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(private[-1]["event"]["payload"], frame)
            self.assertTrue(all(r["event"]["visibility"] == "PRIVATE" for r in private))
            self.assertEqual(frame["physical_model"], "NONE_FIXTURE_ONLY")
            self.assertIsNone(frame["brain"]["action"])

    def test_sensory_cues_and_actions_do_not_expose_hidden_truth(self):
        with self.aquarium() as a:
            frame = a.step()
            contact = frame["toy_contact"]
            self.assertEqual(set(contact["observation"]), {
                "schema", "tick", "world_revision", "visible",
                "reflectance_rgb", "range_bucket_10cm", "source",
            })
            self.assertNotIn("id", contact["observation"])
            self.assertNotIn("softness", contact["observation"])
            self.assertNotIn("toys", contact["observation"])
            self.assertNotIn("hidden_material", contact["prediction_before"])
            self.assertEqual(contact["observation"]["tick"], frame["tick"])
            self.assertEqual(contact["probe_intent"]["tick"], frame["tick"])
            self.assertEqual(contact["probe_intent"]["source_revision"],
                             contact["observation"]["world_revision"])
            self.assertEqual(
                frame["developmental_controller"]["action_source"],
                "internal_committed_prior_outcomes_only",
            )

    def test_prior_observation_changes_future_probe_decision_and_prediction(self):
        with self.aquarium() as a:
            first = a.step()["toy_contact"]
            second = a.step()["toy_contact"]
            self.assertTrue(first["probe_intent"]["requested"])
            self.assertTrue(first["learning"]["learned"])
            self.assertEqual(second["prediction_before"]["predicted_deflection"], 0.75)
            self.assertEqual(second["prediction_before"]["observations"], 1)
            self.assertFalse(second["probe_intent"]["requested"])
            self.assertIsNone(second["measured_contact"])
            self.assertIsNone(second["learning"])
            self.assertEqual(a.contact_model.updates, 1)

    def test_identical_numeric_cues_different_measured_materials_diverge(self):
        rgb = (140, 90, 30)
        soft = ToyScene(toys=(Toy("spectator.a", 1.2, 2.0, .15, rgb, .10),))
        hard = ToyScene(toys=(Toy("spectator.b", 1.2, 2.0, .15, rgb, .90),))
        with self.aquarium(sensory_scene=soft) as a, \
                self.aquarium(self.second, sensory_scene=hard) as b:
            first_a, first_b = a.step(), b.step()
            self.assertEqual(first_a["toy_contact"]["observation"],
                             first_b["toy_contact"]["observation"])
            self.assertEqual(first_a["toy_contact"]["prediction_before"],
                             first_b["toy_contact"]["prediction_before"])
            self.assertEqual(first_a["movement_request"], first_b["movement_request"])
            self.assertEqual(first_a["developmental_controller"], first_b["developmental_controller"])
            self.assertEqual(first_a["toy_contact"]["measured_contact"]["measured_deflection"], .10)
            self.assertEqual(first_b["toy_contact"]["measured_contact"]["measured_deflection"], .90)
            second_a, second_b = a.step(), b.step()
            self.assertEqual(second_a["toy_contact"]["observation"],
                             second_b["toy_contact"]["observation"])
            self.assertEqual(second_a["toy_contact"]["prediction_before"]["predicted_deflection"], .10)
            self.assertEqual(second_b["toy_contact"]["prediction_before"]["predicted_deflection"], .90)

    def test_replay_and_future_frame_identical_after_restart(self):
        with self.aquarium() as a, self.aquarium(self.second) as control:
            for _ in range(30):
                self.assertEqual(a.step(), control.step())
            saved = a.checkpoint()
            self.assertEqual(a.store.count(), 31)
            following = control.step()
        with self.aquarium() as restored:
            self.assertEqual(restored.checkpoint(), saved)
            self.assertEqual(restored.step(), following)
            self.assertEqual(restored.store.count(), 32)

    def test_stale_writer_cannot_change_its_local_memories_or_world(self):
        with self.aquarium() as a, self.aquarium() as stale:
            previous = stale.checkpoint()
            a.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.tick, previous["tick"])
            self.assertEqual(stale.world.revision, previous["world_revision"])
            self.assertEqual(stale.learner.snapshot(), previous["neural_state"])
            self.assertEqual(stale.motor.snapshot(), previous["motor_state"])
            self.assertEqual(stale.contact_model.snapshot(),
                             previous["toy_contact_state"])
            self.assertNotEqual(stale.store.head(), previous["journal_head"])
            with self.assertRaisesRegex(AquariumError, "stale aquarium checkpoint"):
                stale.checkpoint()

    def test_failed_append_cannot_mutate_toy_or_motor_policy(self):
        with self.aquarium() as a:
            before = a.checkpoint()
            with patch.object(a, "_append_record", side_effect=NoosphereError("simulated")):
                with self.assertRaises(NoosphereError):
                    a.step()
            self.assertEqual(a.checkpoint(), before)
            self.assertEqual(a.store.count(), 1)

    def test_crash_after_commit_before_ram_adoption_replays_toy_memory(self):
        with self.aquarium() as a:
            payload, _, _ = a._calculate(None, None)
            a._append_record("aquarium.frame", "frame-000000000000",
                             payload, 0, a.genome.revision)
            self.assertEqual(a.contact_model.updates, 0)
        with self.aquarium() as restored:
            self.assertEqual(restored.tick, 1)
            self.assertEqual(restored.contact_model.updates, 1)
            self.assertEqual(restored.store.count(), 2)

    def test_scene_and_mode_are_part_of_private_bootstrap_identity(self):
        with self.aquarium() as a:
            a.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, motor_mode="developmental_fixture")
        custom = ToyScene(toys=(Toy("changed", 1.2, 2., .15, (200, 55, 30), .3),))
        with self.assertRaises(AquariumError):
            self.aquarium(sensory_scene=custom)
        with self.aquarium() as a:
            self.assertEqual(a.contact_model.updates, 1)

    def test_invalid_sensory_mode_mismatched_scene_and_legacy_mode_rejected(self):
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, sensory_mode="toy_probe")
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, sensory_mode="bad")
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, sensory_mode="none",
                     sensory_scene=ToyScene())
        with self.assertRaises(SensoryBridgeError):
            self.aquarium(sensory_scene=ToyScene(width=5., height=5.))

    def test_toy_disc_inside_or_overlapping_motor_obstacle_rejected_pre_birth(self):
        # Motor room contains the solid vertical barrier x=2.0..2.1,
        # y=1..3. Geometry shared only by dimensions was not sufficient.
        for label, x, radius in (
            ("inside", 2.05, .05),
            ("edge_intersection", 1.97, .05),
            ("far_edge_intersection", 2.13, .05),
        ):
            with self.subTest(label=label):
                scene = ToyScene(toys=(
                    Toy("spectator." + label, x, 2.0,
                        radius, (100, 120, 140), .5),
                ))
                with self.assertRaisesRegex(
                    SensoryBridgeError, "intersects motor-room obstacle",
                ):
                    self.aquarium(sensory_scene=scene)
                self.assertFalse(self.db.exists())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_clear_toy_scene_and_custom_obstacle_free_room_still_replay(self):
        # The same toy is legal in an obstacle-free room, and the normal
        # fixture remains fully usable with the motor-room wall present.
        inside_default_wall = ToyScene(toys=(
            Toy("spectator.unblocked", 2.05, 2.0, .05, (90, 110, 130), .4),
        ))
        with Aquarium(self.second, SEED, GRAPH, room=Room(4, 4),
                      motor_mode="developmental_fixture", sensory_mode="toy_probe",
                      sensory_scene=inside_default_wall) as no_wall:
            self.assertEqual(no_wall.store.count(), 1)
            no_wall.step()
            expected = no_wall.checkpoint()
        with Aquarium(self.second, SEED, GRAPH, room=Room(4, 4),
                      motor_mode="developmental_fixture", sensory_mode="toy_probe",
                      sensory_scene=inside_default_wall) as restored:
            self.assertEqual(restored.checkpoint(), expected)
        with self.aquarium() as default:
            self.assertEqual(default.store.count(), 1)
            default.step()
            self.assertEqual(default.contact_model.updates, 1)

    def test_motor_wall_masks_near_field_toy_and_prevents_probe_intent(self):
        # The toy disc is entirely right of the vertical motor-room wall,
        # but within 30 cm of the agent on the opposite side. The old bridge
        # incorrectly saw its RGB and requested a synthetic probe through it.
        scene = ToyScene(toys=(
            Toy("spectator.hidden", 2.25, 2.0, .10, (250, 1, 3), .97),
        ))
        with self.aquarium(start=(1.98, 2.0), sensory_scene=scene) as a:
            unmasked = scene.sense(a.world)
            self.assertTrue(unmasked["visible"])
            self.assertEqual(unmasked["reflectance_rgb"], [250, 1, 3])
            self.assertEqual(unmasked["range_bucket_10cm"], 2)
            frame = a.step()
            contact = frame["toy_contact"]
            self.assertFalse(contact["observation"]["visible"])
            self.assertIsNone(contact["observation"]["reflectance_rgb"])
            self.assertIsNone(contact["observation"]["range_bucket_10cm"])
            self.assertFalse(contact["probe_intent"]["requested"])
            self.assertIsNone(contact["measured_contact"])
            self.assertEqual(a.contact_model.updates, 0)
            self.assertNotIn("spectator.hidden", str(contact))
            self.assertNotIn("250", str(contact["observation"]))

    def test_unobstructed_further_toy_selected_over_occluded_nearest(self):
        # The visually nearest toy is behind the wall (0.40 m); the visible
        # toy is a little farther to the left (0.55 m). The sensor must not
        # return an invisible nearest object or hide the available one.
        scene = ToyScene(toys=(
            Toy("spectator.occluded", 2.25, 2.0, .10, (255, 5, 5), .9),
            Toy("spectator.visible", 1.30, 2.0, .10, (12, 160, 220), .2),
        ))
        with self.aquarium(start=(1.85, 2.0), sensory_scene=scene) as a:
            self.assertEqual(scene.sense(a.world)["reflectance_rgb"], [255, 5, 5])
            seen = a.step()["toy_contact"]["observation"]
            self.assertTrue(seen["visible"])
            self.assertEqual(seen["reflectance_rgb"], [12, 160, 220])
            self.assertEqual(seen["range_bucket_10cm"], 5)

    def test_wall_mask_is_manifest_pinned_and_replays_exactly(self):
        scene = ToyScene(toys=(
            Toy("spectator.occluded", 2.25, 2.0, .10, (210, 0, 0), .9),
            Toy("spectator.visible", 1.30, 2.0, .10, (20, 160, 230), .2),
        ))
        with self.aquarium(start=(1.85, 2.0), sensory_scene=scene) as first:
            self.assertEqual(first.manifest["occlusion_schema"],
                             "mojum.sensor.center-ray-wall-mask/1")
            for _ in range(8):
                first.step()
            previous = first.checkpoint()
            private = first.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(private[0]["event"]["payload"]["occlusion_schema"],
                             first.manifest["occlusion_schema"])
            self.assertEqual(len(private), 9)
        with self.aquarium(start=(1.85, 2.0), sensory_scene=scene) as restored:
            self.assertEqual(restored.checkpoint(), previous)
            self.assertEqual(restored.store.count(), 9)
            restored.step()
            self.assertEqual(restored.store.count(), 10)

    def test_same_toy_visible_without_wall(self):
        scene = ToyScene(toys=(
            Toy("spectator.unblocked", 2.25, 2.0, .10, (80, 180, 60), .3),
        ))
        with Aquarium(self.second, SEED, GRAPH, room=Room(4, 4),
                      start=(1.98, 2.0), motor_mode="developmental_fixture",
                      sensory_mode="toy_probe", sensory_scene=scene) as a:
            obs = a.step()["toy_contact"]["observation"]
            self.assertTrue(obs["visible"])
            self.assertEqual(obs["reflectance_rgb"], [80, 180, 60])
            self.assertEqual(a.store.count(), 2)

    def test_type_forged_contact_rejected_even_with_valid_journal_chain(self):
        with self.aquarium() as a:
            payload, _, _ = a._calculate(None, None)
            payload["toy_contact"]["measured_contact"]["contact"] = 1
            head = a._append_record("aquarium.frame", "frame-000000000000",
                                    payload, 0, a.genome.revision)
            self.assertEqual(a.store.verify_chain()["head"], head)
        with self.assertRaisesRegex(AquariumError, "mismatched committed frame"):
            self.aquarium(external_expected_head=head)

    def test_pulse_arithmetic_fallback_resets_contact_learning(self):
        with self.aquarium(pulse_sum="ordered") as a:
            for _ in range(30):
                a.step()
            expected = a.checkpoint()

        # Exercise a restart on a host with the other default arithmetic.
        with patch("mojum.aquarium.NATIVE_PULSE_SUM", "compensated"):
            with self.aquarium() as restored:
                self.assertEqual(restored._pulse_sum, "ordered")
                self.assertEqual(restored.checkpoint(), expected)

    def test_huge_rgb_snapshot_key_rejected_by_reused_qa_model(self):
        with self.aquarium() as a:
            malformed = a.contact_model.snapshot()
            malformed["updates"] = 1
            malformed["table"] = {"9" * 5000 + ":0:0":
                                  {"count": 1, "mean_deflection": .5}}
            with self.assertRaises(CuriosityError):
                ContactAssociations.from_snapshot(malformed)

    def test_cli_restarts_the_same_contact_learner(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        cmd = [sys.executable, "-m", "mojum.aquarium", "--db", str(self.db)]
        invalid = subprocess.run(
            [*cmd, "--sensory-mode", "toy_probe"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("requires --motor-mode developmental_fixture", invalid.stderr)
        self.assertFalse(self.db.exists())

        cmd += ["--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                "--neural-mode", "disabled"]
        first = json.loads(subprocess.check_output(
            [*cmd, "--ticks", "1"], cwd=ROOT, env=env, text=True, timeout=30,
        ))
        second = json.loads(subprocess.check_output(
            [*cmd, "--ticks", "1"], cwd=ROOT, env=env, text=True, timeout=30,
        ))
        self.assertEqual((first["tick"], first["stored_events"]), (1, 2))
        self.assertEqual((second["tick"], second["stored_events"]), (2, 3))
        self.assertEqual(second["toy_contact_state"], first["toy_contact_state"])
        self.assertEqual(second["toy_contact_state"]["table"]["220:70:30"], {
            "count": 1, "mean_deflection": 0.75,
        })


if __name__ == "__main__":
    unittest.main()
