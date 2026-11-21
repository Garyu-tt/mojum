"""Mojum neural R0.3: shadow-learning and replay integration tests."""

import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def test_replay_and_continue_both_historical_float_sums(self):
        # Journal heads produced by unmodified main (367ede6), CPython 3.11 and 3.12.
        cases = [
            ("ordered", "compensated", "ff26df18799bb15f74aa066161af6da2862f2388ccc60218406d0b6f136780d4",
             "86ea9617e457190f977effd6fe7a5279ae727a164e3304b9e8a782017d18a039"),
            ("compensated", "ordered", "4329ee42918383e7f0c149a4acf2120eecdbb7753f02419e1ff900a1dbfb4936",
             "579de8af7ac6d372805cdd32770dee9add5b5c0e6dce5aa7c35720f5e47f5607"),
        ]
        for original, other, head120, head150 in cases:
            with self.subTest(original=original):
                db = self.db.with_name(original + ".db")
                with Aquarium(db, SEED, GRAPH, pulse_sum=original) as writer:
                    for _ in range(120):
                        writer.step()
                    before = writer.checkpoint()
                    self.assertEqual(before["journal_head"], head120)
                with self.assertRaises(AquariumError):
                    Aquarium(db, SEED, GRAPH, pulse_sum=other)
                with patch("mojum.aquarium.NATIVE_PULSE_SUM", other):
                    with Aquarium(db, SEED, GRAPH, external_expected_head=head120) as reader:
                        self.assertEqual(reader.checkpoint(), before)
                        for _ in range(30):
                            reader.step()
                        self.assertEqual(reader.checkpoint()["journal_head"], head150)

    def test_alternative_replay_restores_motor_history_and_genome_acquisitions(self):
        with self.aquarium(pulse_sum="ordered", motor_mode="developmental_fixture") as writer:
            writer.step()
            writer.acquire({
                "schema": ACQUISITION_SCHEMA, "event_id": "tempo", "genome_id": SEED["genome_id"],
                "gene": "heartbeat_bpm", "delta": 15, "valid_from_tick": 1,
                "observed_at_ns": 0, "recorded_at_ns": writer.dt_ns,
                "source": "synthetic-unit-test", "evidence": "tempo change",
            })
            for _ in range(25):
                writer.step()
            before = writer.checkpoint()
        with self.assertRaises(AquariumError):
            self.aquarium(pulse_sum="compensated", motor_mode="developmental_fixture")
        with patch("mojum.aquarium.NATIVE_PULSE_SUM", "compensated"):
            with self.aquarium(motor_mode="developmental_fixture") as reader:
                self.assertEqual(reader.checkpoint(), before)

    def test_explicit_source_sum_continues_an_ambiguous_prefix(self):
        with self.aquarium(pulse_sum="ordered") as writer:
            for _ in range(3):
                writer.step()
            before = writer.checkpoint()
        with self.aquarium(pulse_sum="compensated") as other:
            self.assertEqual(other.checkpoint(), before)
        with patch("mojum.aquarium.NATIVE_PULSE_SUM", "compensated"):
            with self.aquarium(pulse_sum="ordered") as reader:
                reader.step()
                self.assertEqual(reader.store.head(), "36e0fbce098d8e9b71ae59570fb02c340141487cc3fddcdf1b86da678de94ce7")

    def test_frame_by_frame_switch_between_sums_is_rejected(self):
        with self.aquarium(pulse_sum="ordered") as a, Aquarium(
            self.db.with_name("other.db"), SEED, GRAPH, pulse_sum="compensated"
        ) as b:
            for _ in range(4):
                a.step()
                b.step()
            b.step()
            fifth = b.store.dataset(include_nonpublic=True)["projection"]["items"][-1]["event"]
            a.store.append(fifth, expected_head=a.store.head())
            mixed_head = a.store.head()
        for native in ("ordered", "compensated"):
            with self.subTest(native=native), patch("mojum.aquarium.NATIVE_PULSE_SUM", native):
                with self.assertRaises(AquariumError):
                    self.aquarium(external_expected_head=mixed_head)

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
            with self.assertRaises(AquariumError):
                a.checkpoint()
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
            self.assertEqual(b.learner.snapshot(), initial)
            with self.assertRaises(AquariumError):
                b.checkpoint()
            self.assertEqual(b.tick, 0)

    def test_writer_advancing_during_learner_snapshot_invalidates_checkpoint(self):
        with self.aquarium() as writer, self.aquarium() as reader:
            snapshot = reader.learner.snapshot

            def snapshot_then_advance():
                state = snapshot()
                writer.step()
                return state

            with patch.object(reader.learner, "snapshot", snapshot_then_advance):
                with self.assertRaises(AquariumError):
                    reader.checkpoint()
            self.assertEqual(reader.tick, 0)
            self.assertEqual(writer.tick, 1)

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
