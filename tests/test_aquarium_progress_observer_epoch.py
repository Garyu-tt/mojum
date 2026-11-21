"""R0.23 actual 10050-frame signed Aquarium v4 epoch + toy histogram QA.

Only disposable synthetic PRIVATE SQLite/archives, never active user data.
A histogram is a summary of recorded fixture activity, not evidence of
consciousness, age progression or intelligence quality.
"""
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.world_room import Room
from mojum.sensory_play import Toy, ToyScene
from mojum.segment_archive import export_sealed_segments
from mojum.live_tail_capsule import capture_live_tail
from mojum.archive_root_receipt import sign_owner_receipt
from mojum.verified_replica_restore import recover_pinned_replica
from mojum.verified_snapshot import create_key_file
from mojum.quarantined_observer import QuarantinedObserver
from mojum.aquarium_progress_observer import summarize_synthetic_progress

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT/"examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT/"examples/first_loop.json").read_text(encoding="utf-8"))
OWNER = "dante-fixture-001"


class LongSyntheticSignedProgressQA(unittest.TestCase):
    def test_10050_actual_motor_ticks_epoch_and_4000_contacts_aggregated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/"PRIVATE-synthetic-original.sqlite3"
            archive = root/"PRIVATE-physical-segments"
            archive.mkdir(mode=0o700)
            creds = root/"PRIVATE-secret-receipts"
            creds.mkdir(mode=0o700)
            key = creds/"hmac-key.bin"
            receipt = creds/"signed-checkpoint.json"
            create_key_file(key)
            restored = root/"PRIVATE-quarantined-readonly.sqlite3"
            scene = ToyScene(
                width=.32, height=.32,
                toys=(Toy("toy.synthetic.fixture", .16,.16,.15,
                          (42,90,174),.4),),
            )
            opts = dict(
                room=Room(.32,.32), start=(.16,.16),
                sensory_scene=scene, sensory_mode="toy_probe_v2",
                developmental_policy="local_cell_v4",
                exploration_mode="goal_free", neural_mode="disabled",
                journal_backend="segmented", journal_segment_size=4096,
            )
            with Aquarium(source, SEED, GRAPH, **opts) as live:
                for _ in range(10050):
                    live.step()
                checkpoint = live.checkpoint()
                head = checkpoint["journal_head"]
                self.assertEqual(live.store.count(), 10052)
                self.assertEqual(live.epoch_index, 1)
                self.assertGreater(checkpoint["toy_contact_state"]["updates"], 4000)
                export_sealed_segments(
                    live.store, archive, include_nonpublic=True,
                    external_expected_head=head,
                )
                capture_live_tail(
                    live.store, archive, include_nonpublic=True,
                    external_expected_source_head=head,
                )
            signed = sign_owner_receipt(
                archive, receipt, key, source_events=10052,
                external_expected_source_head=head,
            )
            # The original disposable source is made unavailable, then
            # only its owner-HMAC verified archived checkpoint is consumed.
            source.rename(root/"source-unavailable.sqlite3")
            recover_pinned_replica(
                archive, restored, receipt, key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
                expected_agent_id=OWNER, allow_private_restore=True,
            )
            with QuarantinedObserver(
                restored, receipt, key,
                independently_pinned_receipt_sha256=signed["receipt_sha256"],
                expected_agent_id=OWNER,
            ) as view:
                redacted = summarize_synthetic_progress(view)
                self.assertNotIn("histogram", redacted)
                self.assertNotIn("motor_frames", redacted)
                report = summarize_synthetic_progress(
                    view, include_private_metrics=True, bins=16,
                )
                self.assertEqual(report["motor_frames"], 10050)
                self.assertEqual(report["journal_events"], 10052)
                self.assertEqual(report["bootstrap_events"], 1)
                self.assertEqual(report["epoch_markers"], 1)
                self.assertEqual(report["source_head"], head)
                self.assertEqual(len(report["histogram"]), 16)
                self.assertEqual(sum(v["frames"] for v in report["histogram"]), 10050)
                self.assertEqual(sum(v["epoch_markers"] for v in report["histogram"]), 1)
                self.assertEqual(
                    report["synthetic_association_updates"],
                    checkpoint["toy_contact_state"]["updates"],
                )
                self.assertGreater(report["measured_synthetic_contacts"], 4000)
                self.assertGreater(report["synthetic_association_updates"], 4000)
                self.assertLessEqual(
                    report["synthetic_association_updates"],
                    report["measured_synthetic_contacts"],
                )
                self.assertEqual(
                    sum(x["association_updates"] for x in report["histogram"]),
                    report["synthetic_association_updates"],
                )
                body = json.dumps(report)
                for forbidden in (
                    "payload", "genome_revision", "movement_request",
                    "toy.synthetic.fixture", "prediction_error",
                ):
                    self.assertNotIn(forbidden, body)
            self.assertFalse(Path(str(restored)+"-wal").exists())
            self.assertFalse(Path(str(restored)+"-shm").exists())


if __name__ == "__main__":
    unittest.main()
