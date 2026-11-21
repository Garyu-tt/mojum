"""Developmental R0.9 low-level perception/touch learning; no preload."""

import copy
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import NoosphereError
from mojum.sensory_curiosity_lab import (
    CuriosityError, ContactAssociations, SensoryPlay, Toy, ToyScene, SENSE_SCHEMA
)
from mojum.world_room import initial_state


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SensoryCuriosityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "sensory.sqlite3"

    def open(self, **kwargs):
        return SensoryPlay(self.path, GRAPH, **kwargs)

    def test_initial_numeric_sense_does_not_disclose_material_or_identity(self):
        scene = ToyScene()
        state = initial_state(scene.room, 1, 2)
        sensor = scene.sense(state)
        self.assertEqual(sensor["schema"], SENSE_SCHEMA)
        self.assertTrue(sensor["visible"])
        self.assertEqual(sensor["reflectance_rgb"], [220, 70, 30])
        self.assertNotIn("id", sensor)
        self.assertNotIn("softness", sensor)
        self.assertNotIn("object", sensor)
        memory = ContactAssociations()
        guess = memory.predict(sensor)
        self.assertEqual(guess["observations"], 0)
        self.assertIsNone(guess["predicted_deflection"])
        self.assertEqual(memory.updates, 0)
        self.assertEqual(memory.table, {})

    def test_no_cue_toy_identity_or_pretrained_object_knowledge(self):
        scene = ToyScene()
        with self.open() as baby:
            self.assertIsNone(baby.manifest["imported_object_knowledge"])
            self.assertIsNone(baby.manifest["goal"])
            self.assertEqual(baby.model.snapshot()["table"], {})
            for _ in range(30):
                frame = baby.step()
                sense = frame["sensor"]
                self.assertEqual(sense["source"], "synthetic_local_proximity_not_camera")
                self.assertNotIn("id", sense)
                self.assertNotIn("softness", sense)
                self.assertEqual(frame["brain_status"], "NOT_A_NEURAL_BRAIN")
                self.assertEqual(frame["inference_scope"],
                                 "observed_numeric_cue_contact_association_only")
                self.assertIsNone(frame["experiment"]["motor_command"])
                self.assertIsNone(frame["experiment"]["goal"])
                self.assertEqual(frame["experiment"]["capability"],
                                 "SYNTHETIC_POINT_ROOM_ONLY")
                self.assertLessEqual(
                    abs(frame["experiment"]["dx"]) + abs(frame["experiment"]["dy"]), .041,
                )
        self.assertEqual(scene.manifest()["spectator_only_toy_identity_and_softness"], True)

    def test_probe_feedback_changes_numeric_prediction_causally(self):
        scene = ToyScene()
        state = initial_state(scene.room, 1, 2)
        sensor = scene.sense(state)
        model = ContactAssociations()
        pred = model.predict(sensor)
        probe = scene.probe(state, sensor)
        self.assertTrue(probe["contact"])
        self.assertEqual(probe["measured_deflection"], .75)
        update = model.learn(sensor, probe, pred)
        self.assertTrue(update["learned"])
        self.assertEqual(update["new_prediction"], .75)
        self.assertEqual(update["evidence_count"], 1)
        self.assertIsNone(update["prediction_error"])
        later = model.predict(sensor)
        self.assertEqual(later["predicted_deflection"], .75)
        update2 = model.learn(sensor, probe, later)
        self.assertEqual(update2["evidence_count"], 2)
        self.assertEqual(update2["prediction_error"], 0.0)
        self.assertEqual(model.updates, 2)

    def test_contact_sensor_context_is_checked_and_noncontact_cannot_fake_reward(self):
        scene = ToyScene()
        state = initial_state(scene.room, 1, 2)
        sensor = scene.sense(state)
        with self.assertRaises(CuriosityError):
            scene.probe(state, dict(sensor, reflectance_rgb=[0, 0, 0]))
        model = ContactAssociations()
        p = model.predict(sensor)
        with self.assertRaises(CuriosityError):
            model.learn(sensor, {"contact": False, "measured_deflection": .9,
                                 "instrument": "synthetic_probe_not_material_classifier",
                                 "source_tick": 0}, p)
        with self.assertRaises(CuriosityError):
            model.learn(sensor, {"contact": True, "measured_deflection": 42,
                                 "instrument": "synthetic_probe_not_material_classifier",
                                 "source_tick": 0}, p)
        self.assertEqual(model.updates, 0)

    def test_no_visible_toy_means_no_leaked_id_or_deflection(self):
        scene = ToyScene()
        sensor = scene.sense(initial_state(scene.room, 3.8, 3.8))
        self.assertFalse(sensor["visible"])
        self.assertIsNone(sensor["reflectance_rgb"])
        self.assertIsNone(sensor["range_bucket_10cm"])
        self.assertIsNone(ContactAssociations().predict(sensor)["cue"])

    def test_scene_and_model_reject_malformed_or_forged_snapshots(self):
        for spec in (
            {"id": "bad", "x": 1, "y": 2, "radius_m": .1,
             "rgb": (255, True, 0), "softness": .2},
            {"id": "bad", "x": 1, "y": 2, "radius_m": .1,
             "rgb": (255, 0, 0), "softness": 2},
            {"id": "", "x": 1, "y": 2, "radius_m": .1,
             "rgb": (255, 0, 0), "softness": .5},
        ):
            with self.subTest(spec=spec):
                with self.assertRaises(CuriosityError):
                    Toy(**spec)
        with self.assertRaises(CuriosityError):
            ToyScene(toys=(Toy("x", 1, 2, .1, (2, 3, 4), .5),
                           Toy("x", 2, 2, .1, (3, 4, 5), .5)))
        model = ContactAssociations()
        base = model.snapshot()
        for forged in (
            dict(base, updates=1),
            dict(base, updates=True),
            dict(base, table={"256:0:0": {"count": 1, "mean_deflection": .5}},
                 updates=1),
            dict(base, table={"1:2:3": {"count": 1,
                                        "mean_deflection": float("nan")}}, updates=1),
            dict(base, table={"1:2:3": {"count": 2, "mean_deflection": .5}},
                 updates=1),
        ):
            with self.subTest(forged=str(forged)[:60]):
                with self.assertRaises(CuriosityError):
                    ContactAssociations.from_snapshot(forged)
        self.assertEqual(model.snapshot(), base)

    def test_real_exploration_observes_contacts_and_retains_memory_after_reopen(self):
        with self.open() as baby:
            rows = [baby.step() for _ in range(120)]
            snapshot = baby.checkpoint()
            self.assertTrue(any(r["contact_result"] is not None
                                and r["contact_result"]["contact"] for r in rows))
            self.assertGreater(baby.model.updates, 0)
            self.assertLessEqual(baby.model.updates, 120)
            self.assertTrue(all(r["spectator_scene"]["spectator_only_toy_identity_and_softness"]
                                for r in rows))
            self.assertEqual(baby.store.count(), 121)
            self.assertEqual(baby.store.dataset()["projection"]["items"], [])
            private = baby.store.dataset(include_nonpublic=True, limit=121)["projection"]["items"]
            self.assertEqual(private[-1]["event"]["payload"], rows[-1])
        with self.open() as baby:
            self.assertEqual(baby.checkpoint(), snapshot)
            baby.step()
            self.assertEqual(baby.tick, 121)

    def test_replay_two_independent_developments_match(self):
        second = self.path.with_name("second.sqlite3")
        with self.open() as a, SensoryPlay(second, GRAPH) as b:
            for _ in range(60):
                self.assertEqual(a.step(), b.step())
            self.assertEqual(a.checkpoint(), b.checkpoint())

    def test_cas_conflict_preserves_local_learner_world_and_rng(self):
        with self.open() as a, self.open() as b:
            old = b.checkpoint()
            a.step()
            with self.assertRaises(NoosphereError):
                b.step()
            self.assertEqual(b.world.revision, old["world_revision"])
            self.assertEqual(b.model.snapshot(), old["association_model"])
            self.assertEqual(b.rng, old["rng"])
            self.assertEqual(b.tick, old["tick"])

    def test_changing_scene_or_seed_must_not_reinterpret_existing_memory(self):
        with self.open() as baby:
            baby.step()
        with self.assertRaises(CuriosityError):
            self.open(rng_seed=42)
        with self.assertRaises(CuriosityError):
            self.open(scene=ToyScene(toys=()))

    def test_no_forged_observation_may_be_committed(self):
        with self.open() as baby:
            original = ToyScene.sense
            before = baby.checkpoint()
            def forged(scene, state):
                result = original(scene, state)
                result["object_semantics"] = "preloaded_true"
                return result
            # Any altered input fails because probe checks the
            # exact permitted near-field contract in the toy scene.
            with patch.object(ToyScene, "sense", forged):
                with self.assertRaises(CuriosityError):
                    baby.step()
            self.assertEqual(baby.checkpoint(), before)
            self.assertEqual(baby.store.count(), 1)

    def test_offline_observer_is_valid_inline_javascript_and_requires_no_network(self):
        page = (ROOT / "examples/sensory_observer.html").read_text(encoding="utf-8")
        class Scripts(HTMLParser):
            def __init__(self):
                super().__init__()
                self.inside = False
                self.scripts = []
                self.external = []
            def handle_starttag(self, tag, attrs):
                if tag == "script":
                    attributes = dict(attrs)
                    if "src" in attributes:
                        self.external.append(attributes["src"])
                    else:
                        self.inside = True
                        self.scripts.append("")
            def handle_endtag(self, tag):
                if tag == "script":
                    self.inside = False
            def handle_data(self, data):
                if self.inside:
                    self.scripts[-1] += data

        parser = Scripts()
        parser.feed(page)
        self.assertEqual(parser.external, [])
        self.assertEqual(len(parser.scripts), 1)
        js = parser.scripts[0]
        for key in (
            "mojum.sensory-curiosity.frame/1",
            "mojum.sensor.synthetic-near-field/1",
            "spectator_only_toy_identity_and_softness",
            "predicted_deflection",
            "measured_deflection",
            "file.text()",
        ):
            self.assertIn(key, js)
        for key in ("fetch(", "WebSocket(", "XMLHttpRequest",
                    "https://", "localStorage", "innerHTML"):
            self.assertNotIn(key, page)
        node = shutil.which("node")
        if node:
            result = subprocess.run(
                [node, "--check", "-"], input=js, text=True,
                capture_output=True, timeout=12,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_restarts_on_same_private_journal(self):
        command = [
            sys.executable, "-m", "mojum.sensory_curiosity_lab",
            "--db", str(self.path), "--ticks", "6", "--watch",
        ]
        out1 = [json.loads(line) for line in subprocess.check_output(command, text=True).splitlines()]
        out2 = [json.loads(line) for line in subprocess.check_output(command, text=True).splitlines()]
        self.assertEqual([x["tick"] for x in out1[:-1]], list(range(6)))
        self.assertEqual([x["tick"] for x in out2[:-1]], list(range(6, 12)))
        self.assertEqual(out2[-1]["checkpoint"]["tick"], 12)
        self.assertEqual(out2[-1]["private_events"], 13)


if __name__ == "__main__":
    unittest.main()
