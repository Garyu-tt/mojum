"""R0.22 owner-HMAC-anchored observer: no mutable SQLite writer opened.

All PRIVATE data is disposable synthetic fixture history. Direct sqlite
immutable/query_only mode and redacted-by-default summaries are tested.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.segmented_noosphere import SegmentedNoosphere
from mojum.segment_archive import export_sealed_segments
from mojum.live_tail_capsule import capture_live_tail
from mojum.archive_root_receipt import sign_owner_receipt
from mojum.verified_snapshot import create_key_file
from mojum.verified_replica_restore import recover_pinned_replica
from mojum.quarantined_observer import QuarantinedObserver, ObserverError
from mojum.aquarium import Aquarium
from mojum.world_room import Room
from test_segmented_noosphere import OWNER, ZERO, record


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT/"examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT/"examples/first_loop.json").read_text(encoding="utf-8"))


class ImmutableRecoveredAquariumObserverQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root/"PRIVATE-synthetic-source.sqlite3"
        self.archive = self.root/"PRIVATE-physical-archive"
        self.archive.mkdir(mode=0o700)
        self.creds = self.root/"PRIVATE-authentication"
        self.creds.mkdir(mode=0o700)
        self.key = self.creds/"owner-32bytes.key"
        self.receipt = self.creds/"owner-receipt.json"
        self.out = self.root/"PRIVATE-quarantined-observe.sqlite3"
        create_key_file(self.key)

    def make(self, count=130, *, unique_topics=False):
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as s:
            head = ZERO
            for i in range(count):
                ev = record(i)
                if unique_topics:
                    ev["topic"] = f"synthetic.topic.{i:04d}"
                head = s.append(ev, expected_head=head)
            export_sealed_segments(
                s, self.archive, include_nonpublic=True,
                external_expected_head=head,
            )
            capture_live_tail(
                s, self.archive, include_nonpublic=True,
                external_expected_source_head=head,
            )
        receipt = sign_owner_receipt(
            self.archive, self.receipt, self.key,
            source_events=count, external_expected_source_head=head,
        )
        recover_pinned_replica(
            self.archive, self.out, self.receipt, self.key,
            independently_pinned_receipt_sha256=receipt["receipt_sha256"],
            expected_agent_id=OWNER, allow_private_restore=True,
        )
        return head, receipt["receipt_sha256"]

    def open_observer(self, digest, *, key=None, agent=OWNER, db=None):
        return QuarantinedObserver(
            self.out if db is None else db,
            self.receipt, self.key if key is None else key,
            independently_pinned_receipt_sha256=digest,
            expected_agent_id=agent,
        )

    def test_default_metadata_redacted_exact_130_count_and_no_writes(self):
        head, digest = self.make()
        before = hashlib.sha256(self.out.read_bytes()).hexdigest()
        with self.open_observer(digest) as q:
            summary = q.summary()
            self.assertEqual(summary["source_head"], head)
            self.assertEqual(summary["journal_events"], 130)
            self.assertEqual(summary["sealed_segments"], 2)
            self.assertEqual(summary["tail_events"], 2)
            self.assertEqual(summary["custody"], "QUARANTINED_READ_ONLY")
            self.assertEqual(summary["privacy"], "private-metadata-redacted")
            self.assertNotIn("topic_counts", summary)
            self.assertNotIn("recent_records", summary)
            with self.assertRaisesRegex(ObserverError, "never permits"):
                q.append(record(130), expected_head=head)
            with self.assertRaisesRegex(ObserverError, "never permits"):
                q.append_many([record(130)], expected_head=head)
            with self.assertRaises(sqlite3.OperationalError):
                q._db.execute("DROP TRIGGER recovery_no_event_insert")
            with self.assertRaises(sqlite3.OperationalError):
                q._db.execute(
                    "INSERT INTO metadata(k,v) VALUES('evil','mutation')"
                )
            self.assertEqual(q.verify_chain(external_expected_head=head)["count"], 130)
        self.assertEqual(hashlib.sha256(self.out.read_bytes()).hexdigest(), before)
        self.assertFalse(Path(str(self.out)+"-wal").exists())
        self.assertFalse(Path(str(self.out)+"-shm").exists())

    def test_explicit_private_metadata_bounded_topics_no_raw_payload(self):
        head, digest = self.make()
        with self.open_observer(digest) as q:
            m = q.summary(include_private_metadata=True, max_recent=3)
            self.assertEqual(sum(m["topic_counts"].values()), 130)
            self.assertEqual(len(m["recent_records"]), 3)
            self.assertEqual(m["recent_records"][0]["seq"], 130)
            self.assertEqual(m["privacy"], "private-metadata-opted-in")
            wire = json.dumps(m)
            self.assertNotIn("PRIVATE_SYNTHETIC", wire)
            self.assertNotIn("classification", wire)
            self.assertNotIn("payload", wire)
            self.assertEqual(m["source_head"], head)
            for wrong in (-1, 65, True, "13"):
                with self.assertRaises(ObserverError):
                    q.summary(include_private_metadata=True, max_recent=wrong)
        self.assertFalse(Path(str(self.out)+"-wal").exists())

    def test_real_130_frame_aquarium_observed_as_event_counts_without_payload(self):
        opts = dict(
            room=Room(.16, .16), start=(.16, .16),
            exploration_mode="goal_free", neural_mode="disabled",
            sensory_mode="none", developmental_policy="local_cell_v4",
            journal_backend="segmented", journal_segment_size=64,
        )
        with Aquarium(self.db, SEED, GRAPH, **opts) as live:
            for _ in range(130):
                live.step()
            original = live.checkpoint()
            head = original["journal_head"]
            self.assertEqual(live.store.count(), 131)
            export_sealed_segments(
                live.store, self.archive, include_nonpublic=True,
                external_expected_head=head,
            )
            capture_live_tail(
                live.store, self.archive, include_nonpublic=True,
                external_expected_source_head=head,
            )
        receipt = sign_owner_receipt(
            self.archive, self.receipt, self.key,
            source_events=131, external_expected_source_head=head,
        )
        recover_pinned_replica(
            self.archive, self.out, self.receipt, self.key,
            independently_pinned_receipt_sha256=receipt["receipt_sha256"],
            expected_agent_id=OWNER, allow_private_restore=True,
        )
        with self.open_observer(receipt["receipt_sha256"]) as observer:
            quiet = observer.summary()
            self.assertEqual(quiet["journal_events"], 131)
            self.assertNotIn("topic_counts", quiet)
            seen = observer.summary(
                include_private_metadata=True, max_recent=4,
            )
            self.assertEqual(seen["topic_counts"]["aquarium.bootstrap"], 1)
            self.assertEqual(seen["topic_counts"]["aquarium.frame"], 130)
            self.assertEqual(sum(seen["topic_counts"].values()), 131)
            self.assertEqual(seen["recent_records"][0]["seq"], 131)
            self.assertEqual(seen["recent_records"][0]["topic"], "aquarium.frame")
            self.assertNotIn("payload", json.dumps(seen))
            self.assertEqual(seen["source_head"], original["journal_head"])
            with self.assertRaises(ObserverError):
                observer.append(record(131), expected_head=head)
        self.assertFalse(Path(str(self.out)+"-wal").exists())

    def test_private_topic_cardinality_bounded_at_64_with_exact_overflow(self):
        _, digest = self.make(unique_topics=True)
        with self.open_observer(digest) as q:
            redacted = q.summary()
            self.assertNotIn("topic_counts", redacted)
            result = q.summary(include_private_metadata=True, max_recent=0)
            self.assertEqual(len(result["topic_counts"]), 64)
            self.assertEqual(sum(result["topic_counts"].values()), 64)
            self.assertEqual(result["other_topics_event_count"], 66)
            self.assertTrue(result["topic_buckets_truncated"])
            self.assertEqual(result["recent_records"], [])
            self.assertEqual(result["journal_events"], 130)
            self.assertNotIn("payload", json.dumps(result))

    def test_wrong_owner_secret_or_receipt_sha_refused(self):
        _, digest = self.make()
        with self.assertRaises(ObserverError):
            self.open_observer(digest, agent="other-agent")
        with self.assertRaises(ValueError):
            self.open_observer("f"*64)
        invalid = self.creds/"different-hmac.key"
        create_key_file(invalid)
        with self.assertRaises(ValueError):
            self.open_observer(digest, key=invalid)

    def test_unsigned_malleable_copy_cannot_fake_quarantine(self):
        _, digest = self.make()
        # Dropping an ordinary SQLite trigger is possible to the owner;
        # read-only observer rejects this forged local state despite
        # matching the original event chain and its signed source head.
        with sqlite3.connect(self.out) as c:
            c.execute("DROP TRIGGER recovery_no_event_insert")
        with self.assertRaisesRegex(ObserverError, "lost quarantine triggers"):
            self.open_observer(digest)

    def test_public_directory_and_symlink_denied_without_writes(self):
        _, digest = self.make()
        public = self.root/"public"
        public.mkdir(mode=0o755)
        foreign = public/"unsafe-private.sqlite3"
        shutil.copy2(self.out, foreign)
        with self.assertRaises(ValueError):
            self.open_observer(digest, db=foreign)
        link = self.root/"alias.sqlite3"
        link.symlink_to(self.out)
        with self.assertRaises(ObserverError):
            self.open_observer(digest, db=link)

    def test_immutable_reader_refuses_existing_wal_or_shm_sidecar(self):
        _, digest = self.make()
        wal = Path(str(self.out)+"-wal")
        wal.write_bytes(b"not-a-checkpoint")
        with self.assertRaisesRegex(ObserverError, "WAL/SHM"):
            self.open_observer(digest)
        wal.unlink()
        with self.open_observer(digest) as q:
            self.assertEqual(q.count(), 130)

    def test_observer_cli_private_metadata_opt_in_and_stdout_redaction(self):
        head, digest = self.make()
        env = dict(os.environ, PYTHONPATH=str(ROOT/"src"))
        args = [
            sys.executable, "-m", "mojum.segment_archive_cli",
            "observe-replica", "--db", str(self.out),
            "--receipt-file", str(self.receipt),
            "--key-file", str(self.key),
            "--expected-receipt-sha", digest,
            "--expected-agent-id", OWNER,
        ]

        def run(*suffix):
            return subprocess.run(
                args+list(suffix), env=env, cwd=ROOT, text=True,
                capture_output=True, timeout=20,
            )

        redacted = run()
        self.assertEqual(redacted.returncode, 0, redacted.stderr)
        first = json.loads(redacted.stdout)
        self.assertEqual(first["source_head"], head)
        self.assertNotIn("topic_counts", first)
        self.assertNotIn("PRIVATE_SYNTHETIC", redacted.stdout)
        explicit = run("--include-private-metadata", "--recent", "4")
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        later = json.loads(explicit.stdout)
        self.assertEqual(later["journal_events"], 130)
        self.assertEqual(len(later["recent_records"]), 4)
        self.assertNotIn("payload", explicit.stdout)
        self.assertNotIn(str(self.out), explicit.stdout)
        self.assertNotIn(str(self.key), explicit.stdout)


if __name__ == "__main__":
    unittest.main()
