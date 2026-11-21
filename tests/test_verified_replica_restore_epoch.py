"""R0.21 full v4 Aquarium epoch + sensory association cold-restore acceptance.

Real 10050 synthetic motor frames, an explicit epoch marker, >4000 toy
contacts, independent owner-HMAC, second PRIVATE archive copy and a
read-only quarantined recovered DB. All filesystem locations are disposable
test fixtures, NOT real independent physical nodes or actual organisms.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.segment_archive import export_sealed_segments
from mojum.live_tail_capsule import capture_live_tail
from mojum.archive_root_receipt import sign_owner_receipt
from mojum.verified_replica_restore import recover_pinned_replica
from mojum.verified_snapshot import create_key_file
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import Room
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT/"examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT/"examples/first_loop.json").read_text(encoding="utf-8"))


class OwnerSignedEpochSensoryColdReplayQA(unittest.TestCase):
    def test_10050_real_frames_signed_replica_epoch_contact_and_readonly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root/"PRIVATE-original-epoch.sqlite3"
            archived = root/"PRIMARY-PRIVATE-physical-segments"
            replica = root/"SECOND-PRIVATE-physical-segments"
            creds = root/"OWNER-PRIVATE-credentials"
            for directory in (archived, replica, creds):
                directory.mkdir(mode=0o700)
            secret = creds/"hmac-secret-32bytes.key"
            signed_file = creds/"sealed-epoch-snapshot.json"
            create_key_file(secret)
            recovered = root/"NEW-QUARANTINED-epoch.sqlite3"
            scene = ToyScene(
                width=.32, height=.32,
                toys=(Toy("toy.synthetic.fixture", .16, .16, .15,
                          (42, 90, 174), .4),),
            )
            options = dict(
                room=Room(.32, .32), start=(.16, .16),
                sensory_scene=scene, exploration_mode="goal_free",
                developmental_policy="local_cell_v4",
                sensory_mode="toy_probe_v2", neural_mode="disabled",
                journal_backend="segmented", journal_segment_size=4096,
            )
            with Aquarium(db, SEED, GRAPH, **options) as live:
                for _ in range(10050):
                    live.step()
                before = live.checkpoint()
                source_head = before["journal_head"]
                self.assertEqual(live.tick, 10050)
                self.assertEqual(live.epoch_index, 1)
                self.assertEqual(live.store.count(), 10052)
                self.assertGreater(live.sensory_bridge.model.updates, 4000)
                sealed = export_sealed_segments(
                    live.store, archived, include_nonpublic=True,
                    external_expected_head=source_head,
                )
                capsule = capture_live_tail(
                    live.store, archived, include_nonpublic=True,
                    external_expected_source_head=source_head,
                )
                self.assertEqual(sealed["sealed_segments"], 2)
                self.assertEqual(sealed["archived_events"], 8192)
                self.assertEqual(capsule["tail_events"], 1860)
                self.assertEqual(capsule["archived_events"], 10052)
            owner = sign_owner_receipt(
                archived, signed_file, secret, source_events=10052,
                external_expected_source_head=source_head,
            )
            for member in archived.iterdir():
                shutil.copy2(member, replica/member.name)
            # Simulate BOTH original journal and primary archive being
            # unavailable; only owner key, owner receipt, pinned digest,
            # and secondary archive remain as intended recovery inputs.
            db.rename(root/"original-now-unavailable.sqlite3")
            archived.rename(root/"first-archive-now-unavailable")
            report = recover_pinned_replica(
                replica, recovered, signed_file, secret,
                independently_pinned_receipt_sha256=owner["receipt_sha256"],
                expected_agent_id=SEED["agent_id"],
                allow_private_restore=True,
            )
            self.assertEqual(report["restored_events"], 10052)
            self.assertEqual(report["restored_tail_events"], 1860)
            self.assertEqual(report["restored_head"], source_head)
            self.assertEqual(report["custody"], "QUARANTINED_READ_ONLY")
            with Aquarium(recovered, SEED, GRAPH, **options) as replay:
                self.assertEqual(replay.checkpoint(), before)
                self.assertEqual(replay.tick, 10050)
                self.assertEqual(replay.epoch_index, 1)
                self.assertGreater(replay.sensory_bridge.model.updates, 4000)
                with self.assertRaises(NoosphereError):
                    replay.step()
                self.assertEqual(replay.tick, 10050)
                self.assertEqual(replay.store.head(), source_head)
                self.assertEqual(replay.store.count(), 10052)


if __name__ == "__main__":
    unittest.main()
