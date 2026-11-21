"""Concurrent-writer regression for PRIVATE development and identity history.

This tests the experimental #150 lineage, not a production or distributed writer.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import LocalNoosphere, NoosphereError

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
IDENTITY = {
    "schema": "mojum.agent.identity/1",
    "agent_id": SEED["genome_id"],
    "display_name": "Данте",
    "assigned_by": "human",
}


class DevelopmentCommittedHeadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "private-aquarium.sqlite3"

    def aquarium(self):
        return Aquarium(self.db, SEED, GRAPH, neural_mode="shadow")

    def test_two_stale_different_development_ranges_cannot_poison_replay(self):
        with self.aquarium() as writer, self.aquarium() as stale:
            old = stale.checkpoint()
            self.assertEqual(writer.advance_development(1), 73)
            # Distinct IDs: WITHOUT a pinned head both writes commit, then
            # future replay rejects the impossible 72->73, 72->74 chain.
            with self.assertRaisesRegex(NoosphereError, "stale event head"):
                stale.advance_development(2)
            self.assertEqual(stale.development_months, 72)
            self.assertEqual(stale.tick, 0)
            self.assertEqual(stale.store.count(), 2)
            self.assertEqual(stale.world.revision, old["world_revision"])
            with self.assertRaisesRegex(AquariumError, "stale"):
                stale.checkpoint()
        with self.aquarium() as recovered:
            self.assertEqual(recovered.development_months, 73)
            self.assertEqual(recovered.store.count(), 2)
            recovered.step()
            self.assertEqual(recovered.tick, 1)

    def test_identity_change_fences_a_stale_development_writer(self):
        with self.aquarium() as owner, self.aquarium() as stale:
            self.assertEqual(owner.assign_human_name(IDENTITY, actor_id="human-owner"),
                             IDENTITY)
            with self.assertRaisesRegex(NoosphereError, "stale event head"):
                stale.advance_development(1)
            self.assertIsNone(stale.identity)
            self.assertEqual(stale.development_months, 72)
            self.assertEqual(owner.checkpoint()["display_name"], "Данте")
        with self.aquarium() as recovered:
            self.assertEqual(recovered.identity, IDENTITY)
            self.assertEqual(recovered.development_months, 72)

    def test_same_writer_can_develop_name_step_and_cold_resume(self):
        with self.aquarium() as writer:
            self.assertEqual(writer.advance_development(2), 74)
            writer.assign_human_name(IDENTITY, actor_id="human-owner")
            self.assertEqual(writer.step()["tick"], 0)
            checkpoint = writer.checkpoint()
            self.assertEqual(writer.store.count(), 4)
            self.assertEqual(checkpoint["development_months"], 74)
        with self.aquarium() as recovered:
            self.assertEqual(recovered.checkpoint(), checkpoint)
            self.assertEqual(recovered.step()["tick"], 1)

    def test_append_between_verify_and_projection_replay_is_rejected(self):
        with self.aquarium() as writer:
            original = LocalNoosphere.dataset

            def projection_then_advance(store, **kwargs):
                snapshot = original(store, **kwargs)
                writer.advance_development(1)
                return snapshot

            with patch.object(LocalNoosphere, "dataset", projection_then_advance):
                with self.assertRaisesRegex(AquariumError, "journal advanced"):
                    self.aquarium()
        with self.aquarium() as recovered:
            self.assertEqual(recovered.development_months, 73)


if __name__ == "__main__":
    unittest.main()
