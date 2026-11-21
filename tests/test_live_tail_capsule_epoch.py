"""Full synthetic v4 motor + contact epoch recovery from physical tail.

One heavy Python 3.13 job: actual 10,050 canonical Aquarium steps, a
committed epoch boundary at 9999 and >4000 empirical contact measures
remain reproducible WITHOUT the original disposable SQLite source.
"""
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.segment_archive import export_sealed_segments
from mojum.live_tail_capsule import (
    capture_live_tail, verify_point_in_time, restore_point_in_time,
)
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class FullVersionedEpochTailRecovery(unittest.TestCase):
    def test_epoch_9999_and_contact_model_rebuilt_from_sealed_plus_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "PRIVATE-synthetic-v4.sqlite3"
            archive = root / "PRIVATE-physical-segments"
            archive.mkdir(mode=0o700)
            recovered = root / "PRIVATE-independent-recovered-v4.sqlite3"
            scene = ToyScene(
                width=.32, height=.32,
                toys=(Toy("toy.synthetic.fixture", .16, .16, .15,
                          (42, 90, 174), .4),),
            )
            opts = dict(
                room=Room(.32, .32), start=(.16, .16),
                sensory_scene=scene,
                exploration_mode="goal_free",
                developmental_policy="local_cell_v4",
                sensory_mode="toy_probe_v2", neural_mode="disabled",
                journal_backend="segmented", journal_segment_size=4096,
            )
            with Aquarium(db, SEED, GRAPH, **opts) as aquarium:
                for _ in range(10050):
                    aquarium.step()
                original = aquarium.checkpoint()
                source_head = original["journal_head"]
                self.assertEqual(aquarium.tick, 10050)
                self.assertEqual(aquarium.epoch_index, 1)
                self.assertEqual(aquarium.store.count(), 10052)
                self.assertGreater(
                    aquarium.sensory_bridge.model.updates, 4000,
                )
                archive_prefix = export_sealed_segments(
                    aquarium.store, archive, include_nonpublic=True,
                    external_expected_head=source_head,
                )
                self.assertEqual(archive_prefix["archived_events"], 8192)
                self.assertEqual(archive_prefix["sealed_segments"], 2)
                tail = capture_live_tail(
                    aquarium.store, archive, include_nonpublic=True,
                    external_expected_source_head=source_head,
                )
                self.assertEqual(tail["tail_events"], 1860)
                self.assertEqual(tail["archived_events"], 10052)
            # Make disposable source unavailable, then recover and resimulate
            # all history directly from immutable physical archive files.
            db.rename(root/"original-out-of-service.sqlite3")
            verified = verify_point_in_time(
                archive, source_events=10052,
                external_expected_source_head=source_head,
            )
            self.assertEqual(verified["source_head"], source_head)
            restored = restore_point_in_time(
                archive, recovered, source_events=10052,
                external_expected_source_head=source_head,
            )
            self.assertEqual(restored["restored_events"], 10052)
            with Aquarium(recovered, SEED, GRAPH, **opts) as replayed:
                self.assertEqual(replayed.checkpoint(), original)
                self.assertEqual(replayed.tick, 10050)
                self.assertEqual(replayed.epoch_index, 1)
                self.assertGreater(replayed.sensory_bridge.model.updates, 4000)
                replayed.step()
                self.assertEqual(replayed.tick, 10051)
                self.assertEqual(replayed.store.count(), 10053)


if __name__ == "__main__":
    unittest.main()
