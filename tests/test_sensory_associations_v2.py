"""Versioned bounded sensory-association QA.

These are synthetic CPU-only tests.  They prove a versioned empirical table,
not a trained brain, object understanding, physical touch, or consciousness.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_wire import inspect_journal
from mojum.sensory_associations_v2 import (
    ContactAssociationsV2,
    LEARNING_SCHEMA,
    MAX_CUES,
    SCHEMA,
    SensoryV2Error,
)
from mojum.sensory_bridge_v2 import REPROBE_PERIOD_TICKS, SCHEMA as BRIDGE_SCHEMA
from mojum.sensory_play import (
    ContactAssociations,
    CuriosityError,
    MODEL_SCHEMA,
    SENSE_SCHEMA,
    Toy,
    ToyScene,
)
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def cue(tick, rgb=(42, 90, 174), visible=True):
    return {
        "schema": SENSE_SCHEMA,
        "tick": tick,
        "world_revision": "a" * 64,
        "visible": visible,
        "reflectance_rgb": list(rgb) if visible else None,
        "range_bucket_10cm": 0 if visible else None,
        "source": "synthetic_local_proximity_not_camera",
    }


def measured(tick, value=0.4, contact=True):
    return {
        "contact": contact,
        "measured_deflection": value if contact else None,
        "instrument": "synthetic_probe_not_material_classifier",
        "source_tick": tick,
    }


class ContactAssociationsV2Tests(unittest.TestCase):
    def test_4001_measured_updates_remain_loadable_and_v1_is_rejected(self):
        model = ContactAssociationsV2()
        for tick in range(4001):
            sensor = cue(tick)
            prediction = model.predict(sensor)
            learning = model.learn(sensor, measured(tick), prediction)
            self.assertEqual(learning["schema"], LEARNING_SCHEMA)

        snapshot = model.snapshot()
        self.assertEqual(snapshot["schema"], SCHEMA)
        self.assertEqual(snapshot["updates"], 4001)
        self.assertEqual(snapshot["table"]["42:90:174"]["count"], 4001)
        self.assertEqual(snapshot["forgotten_observations"], 0)
        self.assertEqual(ContactAssociationsV2.from_snapshot(snapshot).snapshot(), snapshot)
        with self.assertRaises(SensoryV2Error):
            ContactAssociationsV2.from_snapshot(ContactAssociations().snapshot())
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(snapshot)

    def test_deterministic_lru_evicts_oldest_measured_cue(self):
        model = ContactAssociationsV2()
        for tick in range(MAX_CUES):
            sensor = cue(tick, (tick, 0, 0))
            model.learn(sensor, measured(tick), model.predict(sensor))
        refreshed = cue(MAX_CUES, (0, 0, 0))
        model.learn(refreshed, measured(MAX_CUES), model.predict(refreshed))
        newcomer = cue(MAX_CUES + 1, (MAX_CUES, 0, 0))
        result = model.learn(newcomer, measured(MAX_CUES + 1), model.predict(newcomer))

        self.assertEqual(result["evicted_cue"], "1:0:0")
        self.assertEqual(result["evicted_observations"], 1)
        self.assertIn("0:0:0", model.table)
        self.assertNotIn("1:0:0", model.table)
        self.assertEqual(model.evictions, 1)
        self.assertEqual(model.forgotten_observations, 1)
        self.assertEqual(
            ContactAssociationsV2.from_snapshot(model.snapshot()).snapshot(),
            model.snapshot(),
        )

    def test_rejected_update_does_not_mutate_state(self):
        model = ContactAssociationsV2()
        sensor = cue(0)
        model.learn(sensor, measured(0), model.predict(sensor))
        original = model.snapshot()

        forged = measured(1)
        forged["privileged_object_id"] = "spectator.toy.a"
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(1), forged, model.predict(cue(1)))
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(1), measured(1, value=2), model.predict(cue(1)))
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(0), measured(0), model.predict(cue(0)))
        self.assertEqual(model.snapshot(), original)

        bad = deepcopy(original)
        bad["schema"] = MODEL_SCHEMA
        with self.assertRaises(SensoryV2Error):
            ContactAssociationsV2.from_snapshot(bad)


class AquariumSensoryV2Tests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.db = self.root / "sensory-v2.sqlite3"
        self.room = Room(.32, .32)
        self.scene = ToyScene(
            width=.32,
            height=.32,
            toys=(Toy("spectator.synthetic", .16, .16, .15, (42, 90, 174), .4),),
        )

    def aquarium(self, **kwargs):
        return Aquarium(
            self.db,
            SEED,
            GRAPH,
            room=self.room,
            start=(.16, .16),
            neural_mode="disabled",
            motor_mode="developmental_fixture",
            sensory_mode="toy_probe_v2",
            sensory_scene=self.scene,
            **kwargs,
        )

    def test_versioned_mode_uses_one_journal_and_cold_replay(self):
        with self.aquarium() as aquarium:
            first = aquarium.step()
            second = aquarium.step()
            third = aquarium.step()

            self.assertEqual(first["toy_contact"]["schema"], BRIDGE_SCHEMA)
            self.assertEqual(first["toy_contact"]["learning"]["schema"], LEARNING_SCHEMA)
            self.assertIsNone(second["toy_contact"]["learning"])
            self.assertEqual(third["toy_contact"]["learning"]["updates"], 2)
            self.assertEqual(aquarium.store.count(), 4)
            self.assertEqual(
                aquarium.manifest["sensory_reprobe_period_ticks"],
                REPROBE_PERIOD_TICKS,
            )
            aquarium.step()
            saved = aquarium.checkpoint()
            self.assertEqual(aquarium.store.count(), 5)

        with self.aquarium() as restored:
            self.assertEqual(restored.checkpoint(), saved)

    def test_wire_inspector_identifies_versioned_sensory_policy(self):
        with self.aquarium() as aquarium:
            aquarium.step()
            head = aquarium.checkpoint()["journal_head"]
        self.assertEqual(
            inspect_journal(self.db, external_expected_head=head),
            {
                "schema": "mojum.aquarium.wire-inspection/1",
                "format": "motor_play_v1+toy_probe_v2",
                "state_schema": "mojum.developmental.motor-play/1",
                "temporal_schema": None,
                "frames": 1,
                "events": 2,
                "journal_head": head,
                "integrity": "chain-and-independent-head-checked",
                "sensory_schema": SCHEMA,
            },
        )

    def test_wrong_version_refuses_without_reinterpreting_journal(self):
        with self.aquarium() as aquarium:
            aquarium.step()
            before = inspect_journal(self.db)["journal_head"]
        with self.assertRaises(AquariumError):
            Aquarium(
                self.db,
                SEED,
                GRAPH,
                room=self.room,
                start=(.16, .16),
                neural_mode="disabled",
                motor_mode="developmental_fixture",
                sensory_mode="toy_probe",
                sensory_scene=self.scene,
            )
        self.assertEqual(inspect_journal(self.db)["journal_head"], before)


if __name__ == "__main__":
    unittest.main()
