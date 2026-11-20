"""QA for consistent local Aquarium checkpoints under two SQLite writers.

This is an in-process fail-closed consistency gate, NOT a cross-host lock,
trusted remote authority, power-loss guarantee, or secure mesh replication.
"""

import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def acquisition(eid):
    return {
        "schema": ACQUISITION_SCHEMA, "event_id": eid,
        "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm",
        "delta": 10, "valid_from_tick": 0,
        "observed_at_ns": 0, "recorded_at_ns": 0,
        "source": "qa.synthetic", "evidence": "bounded test observation",
    }


class CheckpointHeadFenceQA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "one-organism.sqlite3"

    def open(self, **kwargs):
        return Aquarium(self.path, SEED, GRAPH, exploration_mode="goal_free", **kwargs)

    def test_external_writer_invalidates_stale_checkpoint_before_any_local_step(self):
        with self.open() as left, self.open() as right:
            before = right.checkpoint()
            left.step()
            self.assertNotEqual(left.checkpoint()["journal_head"], before["journal_head"])
            self.assertEqual(right.tick, before["tick"])
            self.assertEqual(right.explorer.snapshot(), before["developmental_state"])
            with self.assertRaisesRegex(AquariumError, "stale or unadopted"):
                right.checkpoint()
            with self.assertRaises(NoosphereError):
                right.step()
            with self.assertRaises(AquariumError):
                right.checkpoint()
        with self.open() as recovered:
            self.assertEqual(recovered.tick, 1)
            self.assertEqual(recovered.explorer.updates, 1)
            self.assertEqual(recovered.checkpoint()["journal_head"],
                             recovered.store.verify_chain()["head"])

    def test_stale_writer_cannot_append_distinct_gene_event(self):
        # Distinct event IDs were previously allowed when _append_record used
        # the newly observed shared head instead of the locally adopted head.
        with self.open() as left, self.open() as right:
            left.step()
            committed = left.checkpoint()
            with self.assertRaisesRegex(NoosphereError, "stale event head"):
                right.acquire(acquisition("stale-distinct-event"))
            self.assertEqual(left.store.count(), 2)
            self.assertEqual(left.checkpoint(), committed)
            with self.assertRaises(AquariumError):
                right.checkpoint()

    def test_external_acquisition_prevents_stale_frame_append(self):
        with self.open() as left, self.open() as right:
            left.acquire(acquisition("new-gene"))
            self.assertEqual(left.store.count(), 2)
            with self.assertRaisesRegex(NoosphereError, "stale event head"):
                right.step()
            self.assertEqual(right.tick, 0)
            self.assertEqual(right.explorer.updates, 0)
            with self.assertRaises(AquariumError):
                right.checkpoint()
        with self.open() as reopened:
            self.assertEqual(reopened.tick, 0)
            self.assertEqual(reopened.store.count(), 2)
            self.assertEqual(
                reopened.genome.phenotype(0)["parameters"]["heartbeat_bpm"], 70,
            )

    def test_append_without_ram_adoption_cannot_be_exported(self):
        # Simulate process death after committed SQLite append but before
        # adopting the next world/policy in RAM.
        with self.open() as baby:
            before = baby.checkpoint()
            event, _, _ = baby._calculate(None, None)
            baby._append_record("aquarium.frame", "frame-000000000000",
                                event, 0, baby.genome.revision)
            self.assertEqual(baby.tick, 0)
            self.assertEqual(baby.explorer.updates, 0)
            self.assertNotEqual(baby.store.head(), before["journal_head"])
            with self.assertRaisesRegex(AquariumError, "stale or unadopted"):
                baby.checkpoint()
        with self.open() as revived:
            self.assertEqual(revived.tick, 1)
            self.assertEqual(revived.explorer.updates, 1)
            self.assertEqual(revived.store.count(), 2)
            self.assertEqual(revived.checkpoint()["journal_head"],
                             revived.store.verify_chain()["head"])

    def test_valid_local_step_and_acquisition_still_checkpoint_exactly(self):
        with self.open() as baby:
            self.assertEqual(baby.checkpoint()["journal_head"], baby.store.head())
            baby.acquire(acquisition("ours"))
            after_gene = baby.checkpoint()
            self.assertEqual(after_gene["journal_head"], baby.store.head())
            self.assertNotEqual(after_gene["genome_revision"], SEED["revision"])
            baby.step()
            after_step = baby.checkpoint()
            self.assertEqual(after_step["journal_head"], baby.store.head())
            self.assertEqual(baby.explorer.updates, 1)
        with self.open() as restored:
            self.assertEqual(restored.checkpoint(), after_step)


if __name__ == "__main__":
    unittest.main()
