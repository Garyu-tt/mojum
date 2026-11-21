"""R0.23 privacy-limited Aquarium progress counters from a trusted observer.

Disposable PRIVATE synthetic source only: actual Aquarium frames -> 128+tail
physical archive -> owner HMAC -> recovered quarantine -> immutable view.
Activity statistics never claim to measure a digital child's intelligence.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium
from mojum.world_room import Room
from mojum.sensory_play import Toy, ToyScene
from mojum.segment_archive import export_sealed_segments
from mojum.live_tail_capsule import capture_live_tail
from mojum.archive_root_receipt import sign_owner_receipt
from mojum.verified_replica_restore import recover_pinned_replica
from mojum.verified_snapshot import create_key_file
from mojum.quarantined_observer import QuarantinedObserver, ObserverError
from mojum.aquarium_progress_observer import summarize_synthetic_progress, SCHEMA
from mojum.segmented_noosphere import SegmentedNoosphere
from test_segmented_noosphere import OWNER, ZERO, record


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT/"examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT/"examples/first_loop.json").read_text(encoding="utf-8"))
AQUARIUM_ACTOR = SEED["genome_id"]


class SyntheticProgressReadOnlyQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.original = self.root/"PRIVATE-fixture.sqlite3"
        self.archive = self.root/"PRIVATE-synthetic-archive"
        self.archive.mkdir(mode=0o700)
        self.credential = self.root/"owner-HMAC"
        self.credential.mkdir(mode=0o700)
        self.key = self.credential/"owner-key.bin"
        self.receipt = self.credential/"pinned-root.json"
        self.recovered = self.root/"PRIVATE-quarantined.sqlite3"
        create_key_file(self.key)

    def _seal_and_recover(self, *, actor, count, head):
        signed = sign_owner_receipt(
            self.archive, self.receipt, self.key,
            source_events=count, external_expected_source_head=head,
        )
        recover_pinned_replica(
            self.archive, self.recovered, self.receipt, self.key,
            independently_pinned_receipt_sha256=signed["receipt_sha256"],
            expected_agent_id=actor, allow_private_restore=True,
        )
        return signed["receipt_sha256"]

    def _observe(self, digest, *, actor=AQUARIUM_ACTOR):
        return QuarantinedObserver(
            self.recovered, self.receipt, self.key,
            independently_pinned_receipt_sha256=digest,
            expected_agent_id=actor,
        )

    def _aquarium(self, steps=130, *, toy=False):
        opts = dict(
            room=Room(.32,.32), start=(.16,.16),
            exploration_mode="goal_free", neural_mode="disabled",
            sensory_mode="toy_probe_v2" if toy else "none",
            developmental_policy="local_cell_v4",
            journal_backend="segmented", journal_segment_size=64,
        )
        if toy:
            opts["sensory_scene"] = ToyScene(
                width=.32, height=.32,
                toys=(Toy("toy.synthetic.fixture", .16, .16, .15,
                          (42,90,174),.4),),
            )
        with Aquarium(self.original, SEED, GRAPH, **opts) as live:
            for _ in range(steps):
                live.step()
            checkpoint = live.checkpoint()
            head = checkpoint["journal_head"]
            count = live.store.count()
            export_sealed_segments(
                live.store, self.archive, include_nonpublic=True,
                external_expected_head=head,
            )
            capture_live_tail(
                live.store, self.archive, include_nonpublic=True,
                external_expected_source_head=head,
            )
        digest = self._seal_and_recover(
            actor=AQUARIUM_ACTOR, count=count, head=head,
        )
        return digest, checkpoint

    def test_default_redaction_does_not_even_iterate_private_events(self):
        digest, _ = self._aquarium()
        with self._observe(digest) as observer:
            with patch.object(observer, "iter_events",
                              side_effect=AssertionError("unsafe iteration")):
                status = summarize_synthetic_progress(observer)
            self.assertEqual(
                status["private_metrics"], "redacted-needs-explicit-opt-in",
            )
            self.assertNotIn("motor_frames", status)
            self.assertNotIn("histogram", status)
            self.assertNotIn("topic_counts", status)

    def test_exact_130_motor_frames_blocking_and_16_bounded_buckets(self):
        digest, checkpoint = self._aquarium()
        with self._observe(digest) as observer:
            result = summarize_synthetic_progress(
                observer, include_private_metrics=True,
            )
            self.assertEqual(result["schema"], SCHEMA)
            self.assertEqual(result["motor_frames"], 130)
            self.assertEqual(result["journal_events"], 131)
            self.assertEqual(result["bootstrap_events"], 1)
            self.assertEqual(result["epoch_markers"], 0)
            self.assertEqual(result["other_source_events"], 0)
            self.assertEqual(result["source_head"], checkpoint["journal_head"])
            self.assertEqual(len(result["histogram"]), 16)
            self.assertEqual(sum(b["frames"] for b in result["histogram"]), 130)
            self.assertEqual(
                sum(b["blocked_frames"] for b in result["histogram"]),
                result["blocked_motor_frames"],
            )
            self.assertEqual(result["requested_synthetic_probes"], 0)
            self.assertEqual(result["measured_synthetic_contacts"], 0)
            self.assertEqual(result["synthetic_association_updates"], 0)
            self.assertIn("not-grades-or-cognition", result["interpretation"])
            self.assertEqual(result["custody"], "QUARANTINED_READ_ONLY")
            wire = json.dumps(result)
            for sensitive in ["payload", "genome_revision", "PRIVATE_SYNTHETIC",
                              "movement_request", "toy.synthetic.fixture"]:
                self.assertNotIn(sensitive, wire)

    def test_real_contact_measurements_agree_with_replayed_learning_update_count(self):
        digest, checkpoint = self._aquarium(toy=True)
        with self._observe(digest) as observer:
            result = summarize_synthetic_progress(
                observer, include_private_metrics=True, bins=8,
            )
        self.assertEqual(result["motor_frames"], 130)
        self.assertEqual(len(result["histogram"]), 8)
        self.assertEqual(result["synthetic_association_updates"],
                         checkpoint["toy_contact_state"]["updates"])
        self.assertGreater(result["synthetic_association_updates"], 0)
        self.assertLessEqual(result["synthetic_association_updates"],
                             result["measured_synthetic_contacts"])
        self.assertLessEqual(result["measured_synthetic_contacts"],
                             result["requested_synthetic_probes"])
        self.assertLessEqual(result["requested_synthetic_probes"], 130)
        self.assertEqual(
            sum(b["association_updates"] for b in result["histogram"]),
            result["synthetic_association_updates"],
        )
        self.assertNotIn("toy.synthetic.fixture", json.dumps(result))

    def test_cli_progress_is_opt_in_and_never_emits_private_event_payload(self):
        digest, checkpoint = self._aquarium(toy=True)
        env = dict(os.environ, PYTHONPATH=str(ROOT/"src"))
        args = [
            sys.executable, "-m", "mojum.segment_archive_cli",
            "observe-replica", "--db", str(self.recovered),
            "--receipt-file", str(self.receipt),
            "--key-file", str(self.key),
            "--expected-receipt-sha", digest,
            "--expected-agent-id", AQUARIUM_ACTOR,
        ]
        def cli(*more):
            return subprocess.run(
                args+list(more), cwd=ROOT, env=env,
                text=True, capture_output=True, timeout=30,
            )
        default = cli()
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertNotIn("motor_frames", default.stdout)
        data = json.loads(default.stdout)
        self.assertEqual(data["journal_events"], 131)
        private = cli("--include-private-progress", "--progress-bins", "8")
        self.assertEqual(private.returncode, 0, private.stderr)
        result = json.loads(private.stdout)
        self.assertEqual(result["motor_frames"], 130)
        self.assertEqual(result["source_head"], checkpoint["journal_head"])
        self.assertEqual(len(result["histogram"]), 8)
        self.assertGreater(result["synthetic_association_updates"], 0)
        for secret in ("payload", "toy.synthetic.fixture", "genome_revision",
                       "movement_request", str(self.key), str(self.recovered)):
            self.assertNotIn(secret, private.stdout)
        both = cli("--include-private-progress", "--include-private-metadata")
        self.assertNotEqual(both.returncode, 0)
        self.assertNotIn("motor_frames", both.stdout)
        excessive = cli("--include-private-progress", "--progress-bins", "33")
        self.assertNotEqual(excessive.returncode, 0)

    def test_non_aquarium_archive_is_not_represented_as_a_developmental_agent(self):
        with SegmentedNoosphere(self.original, OWNER, segment_size=64) as q:
            tip = ZERO
            for i in range(130):
                tip = q.append(record(i), expected_head=tip)
            export_sealed_segments(q, self.archive, include_nonpublic=True,
                                   external_expected_head=tip)
            capture_live_tail(q, self.archive, include_nonpublic=True,
                              external_expected_source_head=tip)
        digest = self._seal_and_recover(actor=OWNER, count=130, head=tip)
        with self._observe(digest, actor=OWNER) as observer:
            with self.assertRaisesRegex(ObserverError, "identifiable canonical Aquarium"):
                summarize_synthetic_progress(
                    observer, include_private_metrics=True,
                )
            redacted = summarize_synthetic_progress(observer)
            self.assertEqual(redacted["journal_events"], 130)
            self.assertNotIn("motor_frames", redacted)

    def test_metrics_are_opt_in_and_bins_strictly_bounded(self):
        digest, _ = self._aquarium()
        with self._observe(digest) as observer:
            for bad in (0, 33, -1, True, "8"):
                with self.assertRaises(ObserverError):
                    summarize_synthetic_progress(
                        observer, include_private_metrics=True, bins=bad,
                    )
            with self.assertRaises(ObserverError):
                summarize_synthetic_progress(
                    observer, include_private_metrics="yes",
                )
            with self.assertRaises(ObserverError):
                summarize_synthetic_progress(
                    "wrong-view", include_private_metrics=True,
                )


if __name__ == "__main__":
    unittest.main()
