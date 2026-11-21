"""Acceptance tests for the explicit synthetic controller boundary."""

import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.developmental_brain import (
    DevelopmentalBrain, DevelopmentalBrainError, SCHEMA, STATUS,
)
from mojum.developmental import MotorPlay


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text())
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text())


class DevelopmentalBrainContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "controller.sqlite3"

    def aquarium(self, **kwargs):
        return Aquarium(
            self.db, SEED, GRAPH, neural_mode="disabled",
            motor_mode="developmental_fixture",
            brain_mode="developmental_controller", **kwargs,
        )

    def test_controller_replaces_placeholder_only_in_explicit_mode(self):
        with self.aquarium(sensory_mode="toy_probe") as aquarium:
            frame = aquarium.step()
            brain = frame["brain"]
            self.assertEqual(brain["schema"], SCHEMA)
            self.assertEqual(brain["status"], STATUS)
            self.assertEqual(brain["action"], frame["developmental_controller"]["intent"])
            self.assertEqual(brain["safety"]["domain"], "synthetic_point_room")
            self.assertFalse(brain["safety"]["external_override"])
            self.assertFalse(brain["safety"]["real_actuation"])
            self.assertEqual(aquarium.checkpoint()["brain_schema"], SCHEMA)

            # The controller sees numeric cues only.  A serialized frame must
            # not accidentally turn spectator-only IDs or material responses
            # into policy inputs.
            wire = json.dumps(brain, sort_keys=True)
            for forbidden in ("spectator.toy", "softness", "obstacles", "toys"):
                self.assertNotIn(forbidden, wire)
            self.assertIn("near_field", brain["input"])

    def test_controller_replay_and_continuation_are_exact(self):
        with self.aquarium(sensory_mode="toy_probe") as writer:
            for _ in range(9):
                writer.step()
            saved = writer.checkpoint()

        with self.aquarium(sensory_mode="toy_probe") as reopened:
            self.assertEqual(reopened.checkpoint(), saved)
            resumed = reopened.step()

        other_db = self.db.with_name("independent.sqlite3")
        with Aquarium(
            other_db, SEED, GRAPH, neural_mode="disabled",
            motor_mode="developmental_fixture",
            brain_mode="developmental_controller",
            sensory_mode="toy_probe",
        ) as independent:
            for _ in range(9):
                independent.step()
            self.assertEqual(independent.step(), resumed)

    def test_explicit_controller_mode_is_pinned_and_cannot_be_reinterpreted(self):
        with self.aquarium() as aquarium:
            aquarium.step()
        with self.assertRaises(AquariumError):
            Aquarium(
                self.db, SEED, GRAPH, neural_mode="disabled",
                motor_mode="developmental_fixture", brain_mode="placeholder",
            )

    def test_external_movement_cannot_bypass_controller_or_change_state(self):
        with self.aquarium() as aquarium:
            before = aquarium.checkpoint()
            with self.assertRaises(AquariumError):
                aquarium.step(dx=0.0)
            self.assertEqual(aquarium.checkpoint(), before)

    def test_privileged_graph_brain_rejected_even_with_controller_enabled(self):
        """An unexpected graph output cannot be masked by the synthetic adapter."""
        from mojum.sim import Simulation

        with self.aquarium() as aquarium:
            prior = aquarium.checkpoint()
            real_step = Simulation.step

            def forged_output(sim):
                output = real_step(sim)
                output[aquarium.kind_ids["brain.placeholder"]]["observation"]["status"] = "ACTIVE"
                return output

            with patch.object(Simulation, "step", forged_output):
                with self.assertRaisesRegex(AquariumError, "unexpected privileged brain behavior"):
                    aquarium.step()
            self.assertEqual(aquarium.checkpoint(), prior)

    def test_controller_bootstrap_rejects_forged_bool_coordinate(self):
        """A rehashed bootstrap must preserve exact start-coordinate JSON type."""
        from mojum.noosphere import _canonical, _sha

        with self.aquarium():
            pass
        with sqlite3.connect(self.db) as db:
            seq, raw, previous = db.execute(
                "SELECT seq,wire_json,prev_hash FROM events WHERE topic='aquarium.bootstrap'"
            ).fetchone()
            event = json.loads(raw)
            coordinate = event["payload"]["room"]["start"][0]
            self.assertEqual(coordinate, 1.0)
            self.assertIs(type(coordinate), float)
            event["payload"]["room"]["start"][0] = True
            self.assertEqual(event["payload"]["room"]["start"][0], coordinate)
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute(
                "UPDATE events SET wire_json=?,event_hash=? WHERE seq=?",
                (_canonical(event), _sha({"previous": previous, "event": event}), seq),
            )
            db.commit()
        with self.assertRaisesRegex(AquariumError, "seed, room, graph, clock or mode mismatch"):
            with self.aquarium():
                pass

    def test_rehashed_controller_safety_flag_rejects_bool_as_int(self):
        """Recomputed local checksum cannot legitimize false -> numeric 0."""
        from mojum.noosphere import _canonical, _sha

        with self.aquarium() as aquarium:
            aquarium.step()
        with sqlite3.connect(self.db) as db:
            row = db.execute(
                "SELECT seq,wire_json,prev_hash FROM events WHERE topic='aquarium.frame'"
            ).fetchone()
            self.assertIsNotNone(row)
            seq, wire, previous = row
            event = json.loads(wire)
            self.assertIs(event["payload"]["brain"]["safety"]["real_actuation"], False)
            event["payload"]["brain"]["safety"]["real_actuation"] = 0
            # The production append-only trigger is disabled ONLY for this
            # disposable corruption fixture.
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute(
                "UPDATE events SET wire_json=?,event_hash=? WHERE seq=?",
                (_canonical(event), _sha({"previous": previous, "event": event}), seq),
            )
            db.commit()
        with self.assertRaisesRegex(AquariumError, "mismatched committed frame"):
            with self.aquarium():
                pass

    def test_direct_contract_rejects_future_stale_and_foreign_sensor_cues(self):
        brain = DevelopmentalBrain()
        motor = MotorPlay()
        heartbeat = {
            "kind": "interoception",
            "heartbeat": {
                "beat_index": 0, "phase_milli": 0,
                "pulse": True, "synthetic": True,
            },
            "observed_at_ns": 0,
        }
        proprioception = {
            "tick": 0, "position": [1.0, 2.0],
            "world_revision": "a" * 64,
        }
        cue = {
            "schema": "mojum.sensor.synthetic-near-field/1",
            "tick": 0, "world_revision": "a" * 64,
            "visible": True, "reflectance_rgb": [1, 2, 3],
            "range_bucket_10cm": 2,
            "source": "synthetic_local_proximity_not_camera",
        }
        original = motor.snapshot()
        for overrides in (
            {"tick": 1},
            {"tick": -1},
            {"world_revision": "b" * 64},
            {"tick": 1, "world_revision": "b" * 64},
        ):
            with self.subTest(overrides=overrides):
                bad_cue = dict(cue, **overrides)
                with self.assertRaises(DevelopmentalBrainError):
                    brain.decide(
                        motor, tick=0, interoception=heartbeat,
                        proprioception=proprioception, near_field=bad_cue,
                    )
                self.assertEqual(motor.snapshot(), original)
        accepted = brain.decide(
            motor, tick=0, interoception=heartbeat,
            proprioception=proprioception, near_field=cue,
        )
        self.assertEqual(accepted["input"]["near_field"], cue)
        self.assertEqual(motor.snapshot(), original)

    def test_direct_contract_rejects_hidden_sensor_metadata(self):
        brain = DevelopmentalBrain()
        interoception = {
            "kind": "interoception",
            "heartbeat": {
                "beat_index": 0, "phase_milli": 0,
                "pulse": True, "synthetic": True,
            },
            "observed_at_ns": 0,
        }
        proprioception = {
            "tick": 0, "position": [1.0, 2.0],
            "world_revision": "a" * 64,
        }
        hidden = {
            "schema": "mojum.sensor.synthetic-near-field/1",
            "tick": 0, "world_revision": "a" * 64,
            "visible": True, "reflectance_rgb": [1, 2, 3],
            "range_bucket_10cm": 2,
            "source": "synthetic_local_proximity_not_camera",
            "softness": 0.5,
        }
        with self.assertRaises(DevelopmentalBrainError):
            brain.decide(
                MotorPlay(), tick=0, interoception=interoception,
                proprioception=proprioception, near_field=hidden,
            )


if __name__ == "__main__":
    unittest.main()
