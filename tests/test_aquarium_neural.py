"""Mojum neural R0.3: shadow-learning and replay integration tests."""

import copy
import json
import tempfile
from pathlib import Path
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.noosphere import NoosphereError

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text())
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text())


class AquariumNeuralTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "aquarium.db"

    def aquarium(self, **kw):
        return Aquarium(self.db, SEED, GRAPH, **kw)

    def test_default_is_shallow_observation_only(self):
        with self.aquarium() as a:
            frame = a.step(dx=0)
            learner = frame["learning_observer"]
            self.assertEqual(learner["status"], "ONLINE_SHADOW_PREDICTOR")
            self.assertFalse(learner["trained"])
            self.assertIsNone(learner["motor_intention"])
            self.assertIsNone(frame["brain"]["action"])
            self.assertEqual(frame["brain"]["status"], "UNIMPLEMENTED")

    def test_delayed_learning_is_journaled(self):
        with self.aquarium() as a:
            a.step(dx=0)
            frame = a.step(dx=0)
            self.assertTrue(frame["learning_observer"]["trained"])
            self.assertEqual(frame["learning_observer"]["updates"], 1)
            events = a.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(events[-1]["event"]["payload"]["learning_observer"], frame["learning_observer"])

    def test_150_tick_resume_preserves_exact_learning_weights(self):
        with self.aquarium() as a:
            for _ in range(120):
                a.step(dx=0)
            start = a.checkpoint()
        with self.aquarium() as a:
            self.assertEqual(a.checkpoint(), start)
            for _ in range(30):
                a.step(dx=0)
            self.assertEqual(a.tick, 150)
            self.assertEqual(a.learner.updates, 149)
            final = a.checkpoint()
        with self.aquarium() as a:
            self.assertEqual(a.checkpoint(), final)

    def test_two_independent_runs_have_identical_weights_and_head(self):
        b = Path(self.temp.name) / "another.db"
        with self.aquarium() as a, Aquarium(b, SEED, GRAPH) as d:
            for _ in range(85):
                self.assertEqual(a.step(dx=0.02), d.step(dx=0.02))
            self.assertEqual(a.checkpoint(), d.checkpoint())

    def test_commit_before_in_memory_change_reconstructs_on_reopen(self):
        with self.aquarium() as a:
            payload, _, _ = a._calculate(0.02, 0)
            a._append_record("aquarium.frame", "frame-000000000000", payload,
                             0, a.genome.revision)
            self.assertEqual(a.tick, 0)
            self.assertEqual(a.learner.updates, 0)
        with self.aquarium() as a:
            self.assertEqual(a.tick, 1)
            self.assertEqual(a.learner.updates, 0)
            self.assertEqual(a.store.count(), 2)
            a.step(dx=0)
            self.assertEqual(a.learner.updates, 1)

    def test_stale_sqlite_head_does_not_mutate_training_state(self):
        with self.aquarium() as a, self.aquarium() as b:
            initial = b.checkpoint()["neural_state"]
            a.step()
            with self.assertRaises(NoosphereError):
                b.step()
            self.assertEqual(b.checkpoint()["neural_state"], initial)
            self.assertEqual(b.tick, 0)

    def test_invalid_movement_cannot_train_or_commit(self):
        with self.aquarium() as a:
            a.step(dx=0)
            state = a.checkpoint()
            with self.assertRaises(AquariumError):
                a.step(dx=float("nan"))
            self.assertEqual(a.checkpoint(), state)

    def test_disabled_mode_never_trains(self):
        with self.aquarium(neural_mode="disabled") as a:
            for _ in range(40):
                frame = a.step()
                self.assertIsNone(frame["learning_observer"])
            self.assertIsNone(a.checkpoint()["neural_state"])
            self.assertEqual(a.tick, 40)
        with self.aquarium(neural_mode="disabled") as a:
            self.assertEqual(a.tick, 40)
            self.assertIsNone(a.learner)

    def test_cannot_switch_neural_mode_on_existing_journal(self):
        with self.aquarium(neural_mode="disabled") as a:
            a.step()
        with self.assertRaises(AquariumError):
            self.aquarium(neural_mode="shadow")

    def test_rejected_neural_mode(self):
        with self.assertRaises(AquariumError):
            self.aquarium(neural_mode="active")

    def test_acquired_genome_does_not_reset_learner(self):
        with self.aquarium() as a:
            for _ in range(10):
                a.step(dx=0)
            old = a.learner.updates
            a.acquire({
                "schema": ACQUISITION_SCHEMA, "event_id": "gain",
                "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm",
                "delta": 15, "valid_from_tick": a.tick,
                "observed_at_ns": 0, "recorded_at_ns": a.tick * a.dt_ns,
                "source": "synthetic-unit-test", "evidence": "not biology",
            })
            self.assertEqual(a.learner.updates, old)
            a.step(dx=0)
            self.assertEqual(a.learner.updates, old + 1)
            state = a.checkpoint()
        with self.aquarium() as a:
            self.assertEqual(a.checkpoint(), state)

    def test_journal_private_and_body_unactuated(self):
        with self.aquarium() as a:
            a.step(dx=0.5)
            self.assertEqual(len(a.store.dataset()["projection"]["items"]), 0)
            rows = a.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertTrue(all(r["event"]["visibility"] == "PRIVATE" for r in rows))
            frame = rows[-1]["event"]["payload"]
            self.assertEqual(frame["physical_model"], "NONE_FIXTURE_ONLY")
            self.assertIsNone(frame["learning_observer"]["motor_intention"])
            self.assertFalse(frame["world"]["physical_contact"])

    def test_explicit_neural_state_separated_from_placeholder_brain(self):
        with self.aquarium() as a:
            a.step()
            cp = a.checkpoint()
            self.assertEqual(cp["neural_mode"], "shadow")
            self.assertEqual(cp["neural_state"]["schema"], "mojum.neural.delayed-pulse/1")
            self.assertEqual(a.graph["nodes"][4]["kind"], "brain.placeholder")

    def test_checkpoint_is_copied(self):
        with self.aquarium() as a:
            a.step()
            snapshot = a.checkpoint()
            snapshot["neural_state"]["weights"][0] = 200
            self.assertNotEqual(a.checkpoint()["neural_state"]["weights"][0], 200)


if __name__ == "__main__":
    unittest.main()
