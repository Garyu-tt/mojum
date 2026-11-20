"""Regression for in-process and SQLite local writer freeze during cold capture."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.cold_aquarium import capture_quiescent_aquarium
from mojum.genesis import ACQUISITION_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples" / "genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))


def gene_event(tick):
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": "quiescence-gene",
        "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm",
        "delta": 10,
        "valid_from_tick": tick,
        "observed_at_ns": 0,
        "recorded_at_ns": tick * 10_000_000,
        "source": "synthetic-freeze-test",
        "evidence": "fixture-only",
    }


class AquariumCheckpointFreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "source.sqlite3"

    def aquarium(self):
        return Aquarium(self.db, SEED, GRAPH)

    def test_step_and_acquire_refused_without_changes_during_freeze(self):
        with self.aquarium() as q:
            q.step()
            expected = q.checkpoint()
            with q.freeze_checkpoint() as stamp:
                self.assertEqual(stamp["checkpoint"], expected)
                self.assertEqual(stamp["event_count"], 2)
                with self.assertRaisesRegex(AquariumError, "writes forbidden"):
                    q.step(dx=0.05)
                with self.assertRaisesRegex(AquariumError, "writes forbidden"):
                    q.acquire(gene_event(q.tick))
                self.assertEqual(q.checkpoint(), expected)
                self.assertEqual(q.store.count(), 2)
            self.assertFalse(q._freeze_active)
            q.step()
            self.assertEqual(q.tick, 2)

    def test_other_local_sqlite_writer_cannot_commit_during_freeze(self):
        with self.aquarium() as q:
            with q.freeze_checkpoint():
                with sqlite3.connect(str(self.db), timeout=0.025,
                                     isolation_level=None) as other:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("BEGIN IMMEDIATE")
                self.assertEqual(q.store.head(), q.checkpoint()["journal_head"])
            # Reservation was released: a second connection can now take it.
            with sqlite3.connect(str(self.db), timeout=0.025,
                                 isolation_level=None) as other:
                other.execute("BEGIN IMMEDIATE")
                other.execute("ROLLBACK")
            q.step()
            self.assertEqual(q.tick, 1)

    def test_nested_freeze_refused_and_close_guarded(self):
        with self.aquarium() as q:
            with q.freeze_checkpoint():
                with self.assertRaisesRegex(AquariumError, "recursive"):
                    with q.freeze_checkpoint():
                        pass
                with self.assertRaisesRegex(AquariumError, "cannot close"):
                    q.close()
            self.assertFalse(q._freeze_active)
            q.step()

    def test_failure_in_captured_body_releases_transaction(self):
        with self.aquarium() as q:
            with patch("mojum.cold_aquarium._capture_frozen_body",
                       side_effect=ValueError("simulated capture error")):
                with self.assertRaisesRegex(ValueError, "simulated capture"):
                    capture_quiescent_aquarium(q)
            self.assertFalse(q._freeze_active)
            q.step()
            self.assertEqual(q.store.count(), 2)

    def test_capture_function_enters_freeze_before_reading_components(self):
        with self.aquarium() as q:
            q.step(dx=0.01)
            def check_frozen(instance):
                self.assertTrue(instance._freeze_active)
                self.assertTrue(instance.store._db.in_transaction)
                with self.assertRaises(AquariumError):
                    instance.step()
                return {"captured_during_freeze": True}
            with patch("mojum.cold_aquarium._capture_frozen_body",
                       side_effect=check_frozen):
                result = capture_quiescent_aquarium(q)
            self.assertTrue(result["captured_during_freeze"])
            self.assertFalse(q.store._db.in_transaction)

    def test_external_commit_to_stale_instance_prevents_freeze(self):
        with self.aquarium() as stale, self.aquarium() as active:
            active.step(dx=0.01)
            self.assertEqual(stale.tick, 0)
            with self.assertRaisesRegex(AquariumError, "differs from local replayed"):
                with stale.freeze_checkpoint():
                    self.fail("stale snapshot must not be exposed")
            self.assertFalse(stale.store._db.in_transaction)
            # Reopen from the current journal instead of inventing continuity.
        with self.aquarium() as reopened:
            self.assertEqual(reopened.tick, 1)
            with reopened.freeze_checkpoint() as stamp:
                self.assertEqual(stamp["event_count"], 2)

    def test_stale_writer_cannot_append_even_different_acquisition_event(self):
        with self.aquarium() as stale, self.aquarium() as active:
            active.step(dx=0.01)
            with self.assertRaises(ValueError):
                stale.acquire(gene_event(stale.tick))
            self.assertEqual(stale.tick, 0)
            self.assertEqual(stale.store.count(), 2)

    def test_mutated_state_detected_at_exit(self):
        with self.aquarium() as q:
            with self.assertRaisesRegex(AquariumError, "changed during checkpoint freeze"):
                with q.freeze_checkpoint():
                    q.tick += 1  # deliberate internal bypass, never a valid API
            self.assertFalse(q.store._db.in_transaction)
            self.assertFalse(q._freeze_active)

    def test_full_existing_cold_capture_still_verified(self):
        from mojum.checkpoint_bundle import unpack
        with self.aquarium() as q:
            for _ in range(4):
                q.step(dx=.01)
            expected = copy.deepcopy(q.checkpoint())
            bundle = capture_quiescent_aquarium(q)
            parts = unpack(bundle, expected_revision=4,
                           expected_organism_id=SEED["genome_id"])
            self.assertEqual(json.loads(parts["neural"])["state"],
                             expected["neural_state"])
            self.assertEqual(json.loads(parts["memory"])["head"],
                             expected["journal_head"])
            self.assertEqual(q.checkpoint(), expected)


if __name__ == "__main__":
    unittest.main()
