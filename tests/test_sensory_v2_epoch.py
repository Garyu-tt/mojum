"""R0.15 versioned contact memory beyond 4000 observations + epoch replay.

Only disposable synthetic PRIVATE SQLite and no real devices, emotions,
privileged toy identities, external actuators or user memory.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.sensory_play import (
    ContactAssociations, CuriosityError, MODEL_SCHEMA,
    SENSE_SCHEMA, Toy, ToyScene,
)
from mojum.sensory_associations_v2 import (
    ContactAssociationsV2, SensoryV2Error, SCHEMA as MODEL_V2_SCHEMA,
    LEARNING_SCHEMA as LEARNING_V2_SCHEMA, MAX_CUES,
)
from mojum.sensory_bridge import SensoryBridgeError
from mojum.sensory_bridge_v2 import (
    ToyContactBridgeV2, SCHEMA as BRIDGE_V2_SCHEMA,
)
from mojum.verified_snapshot import (
    SnapshotError, create_key_file, load_snapshot, save_snapshot,
)
from mojum.world_room import Room, initial_state


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def cue(tick, rgb=(42, 90, 174), visible=True):
    return {
        "schema": SENSE_SCHEMA, "tick": tick,
        "world_revision": "a" * 64, "visible": visible,
        "reflectance_rgb": list(rgb) if visible else None,
        "range_bucket_10cm": 0 if visible else None,
        "source": "synthetic_local_proximity_not_camera",
    }


def measured(tick, value=.4, contact=True):
    return {
        "contact": contact,
        "measured_deflection": value if contact else None,
        "instrument": "synthetic_probe_not_material_classifier",
        "source_tick": tick,
    }


class BoundedContactUnitQA(unittest.TestCase):
    def test_4401_observations_and_v1_schema_refusal(self):
        a = ContactAssociationsV2()
        for tick in range(4401):
            sensor = cue(tick)
            predicted = a.predict(sensor)
            result = a.learn(sensor, measured(tick), predicted)
            self.assertEqual(result["schema"], LEARNING_V2_SCHEMA)
            self.assertEqual(result["updates"], tick + 1)
        saved = a.snapshot()
        self.assertEqual(saved["schema"], MODEL_V2_SCHEMA)
        self.assertEqual(saved["updates"], 4401)
        self.assertEqual(saved["table"]["42:90:174"]["count"], 4401)
        self.assertEqual(saved["forgotten_observations"], 0)
        self.assertEqual(
            ContactAssociationsV2.from_snapshot(saved).snapshot(), saved,
        )
        with self.assertRaises(CuriosityError):
            ContactAssociations.from_snapshot(saved)
        with self.assertRaises(SensoryV2Error):
            ContactAssociationsV2.from_snapshot(
                ContactAssociations().snapshot(),
            )

    def test_130_color_cues_eviction_and_recent_refreshed_128_cap(self):
        a = ContactAssociationsV2()
        for tick in range(MAX_CUES):
            sensor = cue(tick, (tick, 0, 0))
            a.learn(sensor, measured(tick), a.predict(sensor))
        self.assertEqual(len(a.table), MAX_CUES)
        refresh = cue(MAX_CUES, (0, 0, 0))
        a.learn(refresh, measured(MAX_CUES), a.predict(refresh))
        self.assertEqual(a.last_seen_at["0:0:0"], MAX_CUES)
        nextcue = cue(MAX_CUES + 1, (MAX_CUES, 0, 0))
        result = a.learn(nextcue, measured(MAX_CUES + 1),
                         a.predict(nextcue))
        self.assertEqual(result["evicted_cue"], "1:0:0")
        self.assertIn("0:0:0", a.table)
        self.assertNotIn("1:0:0", a.table)
        self.assertEqual(len(a.table), MAX_CUES)
        self.assertEqual(a.evictions, 1)
        self.assertEqual(a.forgotten_observations, 1)
        saved = a.snapshot()
        self.assertEqual(
            ContactAssociationsV2.from_snapshot(saved).snapshot(), saved,
        )
        probe = cue(MAX_CUES + 2, (MAX_CUES + 1, 0, 0))
        next_result = a.learn(probe, measured(MAX_CUES + 2),
                              a.predict(probe))
        self.assertEqual(next_result["evicted_cue"], "2:0:0")
        self.assertEqual(len(a.table), MAX_CUES)
        self.assertEqual(a.evictions, 2)
        self.assertEqual(a.forgotten_observations, 2)

    def test_corrupt_snapshots_invalid_and_no_partial_learning(self):
        model = ContactAssociationsV2()
        model.learn(cue(2), measured(2), model.predict(cue(2)))
        original = model.snapshot()
        for field, altered in (
            ("schema", MODEL_SCHEMA),
            ("updates", True),
            ("updates", -1),
            ("last_seen_at", {}),
            ("last_seen_at", {"42:90:174": True}),
            ("last_seen_at", {"42:90:174": 100}),
            ("last_contact_tick", 1),
            ("forgotten_observations", 1),
            ("evictions", 1),
            ("table", {"042:90:174": {"count": 1, "mean_deflection": .4}}),
            ("table", {"42:90:174": {"count": True, "mean_deflection": .4}}),
            ("table", {"42:90:174": {
                "count": 1, "mean_deflection": 10**1000,
            }}),
        ):
            with self.subTest(field=field, value=altered):
                bad = deepcopy(original)
                bad[field] = altered
                with self.assertRaises(SensoryV2Error):
                    ContactAssociationsV2.from_snapshot(bad)
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(2), measured(2), model.predict(cue(2)))
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(3), measured(3, 9), model.predict(cue(3)))
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(3), measured(3, 10**1000),
                        model.predict(cue(3)))
        forged = measured(3)
        forged["privileged_object_id"] = "secret-identification"
        with self.assertRaises(SensoryV2Error):
            model.learn(cue(3), forged, model.predict(cue(3)))
        self.assertEqual(model.snapshot(), original)

    def test_candidate_only_mutates_after_verified_sensory_adoption(self):
        room = Room(.32, .32)
        scene = ToyScene(
            width=.32, height=.32,
            toys=(Toy("spectator.synthetic", .16, .16, .15,
                      (42, 90, 174), .4),),
        )
        bridge = ToyContactBridgeV2(scene, motor_room=room)
        state = initial_state(room, .16, .16)
        row, candidate = bridge.evaluate(state)
        self.assertEqual(row["schema"], BRIDGE_V2_SCHEMA)
        self.assertEqual(row["learning"]["schema"], LEARNING_V2_SCHEMA)
        self.assertEqual(row["probe_intent"]["external_actuator"], None)
        self.assertEqual(bridge.model.updates, 0)
        self.assertEqual(candidate.updates, 1)
        altered = deepcopy(row)
        altered["association_state_sha256"] = "0" * 64
        with self.assertRaises(SensoryBridgeError):
            bridge.adopt(state, altered)
        self.assertEqual(bridge.model.updates, 0)
        bridge.adopt(state, row)
        self.assertEqual(bridge.model.updates, 1)


class R015FullSensoryEpochSQLiteQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "private-synthetic-v4.sqlite3"
        self.snap = self.root / "private-synthetic.replay.json"
        self.key = self.root / "private-synthetic.key"
        create_key_file(self.key)
        self.room = Room(.32, .32)
        self.scene = ToyScene(
            width=.32, height=.32,
            toys=(Toy("spectator.synthetic", .16, .16, .15,
                      (42, 90, 174), .4),),
        )

    def open(self, *, policy="local_cell_v4", sensory="toy_probe_v2",
             signed=True):
        kw = {}
        if signed:
            kw.update(
                replay_snapshot_file=self.snap,
                replay_snapshot_key_file=self.key,
            )
        return Aquarium(
            self.db, SEED, GRAPH,
            room=self.room, start=(.16, .16),
            sensory_scene=self.scene if sensory != "none" else None,
            exploration_mode="goal_free", neural_mode="disabled",
            developmental_policy=policy, sensory_mode=sensory, **kw,
        )

    def test_real_10050_frames_and_over_4000_contacts_across_epoch(self):
        with self.open() as a:
            self.assertEqual(a.manifest["sensory_mode"], "toy_probe_v2")
            self.assertEqual(a.manifest["sensory_model_schema"],
                             MODEL_V2_SCHEMA)
            self.assertEqual(a.manifest["contact_bridge_schema"],
                             BRIDGE_V2_SCHEMA)
            self.assertEqual(a.manifest["sensor_reprobe_period_ticks"], 2)
            for i in range(8050):
                frame = a.step()
                if i == 0:
                    self.assertEqual(frame["toy_contact"]["schema"],
                                     BRIDGE_V2_SCHEMA)
            self.assertEqual(a.tick, 8050)
            self.assertGreater(a.sensory_bridge.model.updates, 4000)
            signed_at = a.checkpoint()
            a.save_replay_snapshot()
            self.assertEqual(
                load_snapshot(self.snap, self.key)["checkpoint"], signed_at
            )
            for _ in range(2000):
                a.step()
            final = a.checkpoint()
            self.assertEqual(a.tick, 10050)
            self.assertEqual(a.epoch_index, 1)
            self.assertGreater(a.sensory_bridge.model.updates, 5000)
            self.assertEqual(a.sensory_bridge.model.evictions, 0)
            self.assertEqual(a.sensory_bridge.model.snapshot()["schema"],
                             MODEL_V2_SCHEMA)
            self.assertEqual(a.store.count(), 10052)
            self.assertEqual(a.store.event_at(10001)["event"]["topic"],
                             "aquarium.epoch")
            self.assertEqual(final["epoch_index"], 1)
            self.assertEqual(final["toy_contact_state"]["updates"],
                             a.sensory_bridge.model.updates)
            final_head = final["journal_head"]
        with self.open() as fast:
            self.assertEqual(fast.checkpoint(), final)
            self.assertEqual(fast.store.head(), final_head)
            self.assertEqual(fast.sensory_bridge.model.snapshot(),
                             final["toy_contact_state"])
        with self.open(signed=False) as full:
            self.assertEqual(full.checkpoint(), final)
            self.assertEqual(full.store.verify_chain()["head"], final_head)
            self.assertEqual(full.tick, 10050)

    def test_validly_signed_legacy_sensor_state_fails_without_sqlite_mutation(self):
        with self.open() as a:
            for _ in range(25):
                a.step()
            expected = a.checkpoint()
            a.save_replay_snapshot()
        signed = load_snapshot(self.snap, self.key)
        self.assertEqual(signed["toy_contact_state"]["schema"],
                         MODEL_V2_SCHEMA)
        signed["toy_contact_state"]["schema"] = MODEL_SCHEMA
        # A holder of the local HMAC secret can sign a malformed model;
        # the exact bootstrap model contract must still reject it.
        save_snapshot(self.snap, self.key, signed)
        with self.assertRaises((ValueError, SnapshotError)):
            with self.open():
                pass
        with self.open(signed=False) as a:
            self.assertEqual(a.checkpoint(), expected)
            self.assertEqual(a.store.count(), 26)

    def test_actual_foreground_host_v4_sensor_two_signed_sessions(self):
        # Portable CPython/Termux-style run, but no actual Android device.
        # The portable host uses the default 4m fixture, not this class's
        # special contact-rich 0.32m test room.
        db = self.root / "portable-synthetic.sqlite3"
        snap = self.root / "portable-synthetic.snapshot"
        key = self.root / "portable-synthetic.key"
        create_key_file(key)
        command = [
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(db), "--ticks", "3", "--interval-ms", "20",
            "--exploration-mode", "goal_free",
            "--neural-mode", "disabled",
            "--developmental-policy", "local_cell_v4",
            "--sensory-mode", "toy_probe_v2",
            "--snapshot-file", str(snap),
            "--snapshot-key-file", str(key),
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        results = []
        for expected_begin in (0, 3):
            done = subprocess.run(
                command, cwd=ROOT, env=env, text=True,
                capture_output=True, timeout=18,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            info = json.loads(done.stdout)
            self.assertEqual(info["start_tick"], expected_begin)
            self.assertEqual(info["end_tick"], expected_begin + 3)
            self.assertEqual(info["journal_events"], expected_begin + 4)
            self.assertNotIn("toy_contact_state", done.stdout)
            self.assertNotIn("measured_deflection", done.stdout)
            results.append(info)
        self.assertNotEqual(results[0]["journal_head"],
                            results[1]["journal_head"])
        with Aquarium(
            db, SEED, GRAPH, exploration_mode="goal_free",
            developmental_policy="local_cell_v4",
            sensory_mode="toy_probe_v2", neural_mode="disabled",
            replay_snapshot_file=snap, replay_snapshot_key_file=key,
        ) as q:
            self.assertEqual(q.tick, 6)
            self.assertEqual(q.store.count(), 7)
            self.assertEqual(q.sensory_bridge.model.snapshot()["schema"],
                             MODEL_V2_SCHEMA)

    def test_old_mode_and_wrong_policy_rejected_without_history_change(self):
        with self.open() as a:
            for _ in range(10):
                a.step()
            checkpoint = a.checkpoint()
        for config in (
            {"sensory": "none"},
            {"sensory": "toy_probe"},
            {"policy": "local_cell_v3"},
        ):
            with self.subTest(config=config):
                with self.assertRaises((ValueError, AquariumError)):
                    with self.open(**config):
                        pass
        with self.open() as a:
            self.assertEqual(a.checkpoint(), checkpoint)
            self.assertEqual(a.store.count(), 11)


if __name__ == "__main__":
    unittest.main()
