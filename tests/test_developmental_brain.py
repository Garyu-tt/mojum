"""Acceptance tests for the explicit synthetic controller boundary."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.developmental_brain import (
    DevelopmentalBrain, DevelopmentalBrainError, SCHEMA, STATUS,
)
from mojum.developmental import MotorPlay
from mojum.sim import Simulation


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

    def test_controller_bootstrap_rejects_bool_for_integer_field(self):
        # Python considers False == 0, but the persisted opt-in contract must
        # reject JSON type substitution even when the other event fields match.
        with self.aquarium() as aquarium:
            expected = aquarium._record(
                "aquarium.bootstrap", "aquarium-bootstrap",
                aquarium.manifest, 0, aquarium.graph_sha256,
            )
            forged = copy.deepcopy(expected)
            forged["observed_at_ns"] = False
            with self.assertRaises(AquariumError):
                aquarium._check_bootstrap(forged)

    def test_controller_frame_replay_rejects_bool_for_integer_field(self):
        with self.aquarium() as aquarium:
            aquarium.step()
            items = copy.deepcopy(
                aquarium.store.dataset(include_nonpublic=True)["projection"]["items"]
            )
            items[1]["event"]["payload"]["brain"]["input"]["interoception"][
                "observed_at_ns"
            ] = False
            aquarium._reset_replay_state(aquarium._pulse_sum)
            with self.assertRaises(AquariumError):
                aquarium._replay_events(items)

    def test_controller_rejects_privileged_graph_output_before_override(self):
        with self.aquarium() as aquarium:
            before = aquarium.checkpoint()
            original_step = Simulation.step

            def privileged_graph_step(runtime):
                output = original_step(runtime)
                observation = output[aquarium.kind_ids["brain.placeholder"]]["observation"]
                observation["status"] = "PRIVILEGED"
                observation["action"] = {"kind": "x+"}
                return output

            with patch("mojum.aquarium.Simulation.step", new=privileged_graph_step):
                with self.assertRaises(AquariumError):
                    aquarium.step()
            self.assertEqual(aquarium.checkpoint(), before)

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
                        motor, tick=0, expected_at_ns=0,
                        interoception=heartbeat,
                        proprioception=proprioception, near_field=bad_cue,
                    )
                self.assertEqual(motor.snapshot(), original)
        future_heartbeat = dict(heartbeat, observed_at_ns=10_000_000)
        with self.assertRaises(DevelopmentalBrainError):
            brain.decide(
                motor, tick=0, expected_at_ns=0,
                interoception=future_heartbeat,
                proprioception=proprioception, near_field=cue,
            )
        self.assertEqual(motor.snapshot(), original)
        accepted = brain.decide(
            motor, tick=0, expected_at_ns=0,
            interoception=heartbeat,
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
                MotorPlay(), tick=0, expected_at_ns=0,
                interoception=interoception,
                proprioception=proprioception, near_field=hidden,
            )


if __name__ == "__main__":
    unittest.main()
