"""R0.21 signed cold replica restore without original canonical SQLite.

Disposable 0700 folders and synthetic private records only. Copying two
paths in one test filesystem is NOT physically independent redundancy.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import NoosphereError
from mojum.segment_archive import ArchiveError, export_sealed_segments, names
from mojum.live_tail_capsule import capture_live_tail
from mojum.segmented_noosphere import SegmentedNoosphere
from mojum.archive_root_receipt import sign_owner_receipt, ReceiptError
from mojum.verified_replica_restore import recover_pinned_replica
from mojum.verified_snapshot import create_key_file
from mojum.aquarium import Aquarium
from mojum.world_room import Room
from test_segmented_noosphere import OWNER, ZERO, record


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT/"examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT/"examples/first_loop.json").read_text(encoding="utf-8"))


class SignedHistoricalCopyRecoveryQA(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.root = Path(sandbox.name)
        self.original = self.root/"PRIVATE-original.sqlite3"
        self.copy_a = self.root/"first-owner-archive"
        self.copy_b = self.root/"second-owner-archive"
        self.secrets = self.root/"private-signature"
        for folder in (self.copy_a, self.copy_b, self.secrets):
            folder.mkdir(mode=0o700)
        self.key = self.secrets/"owner-hmac-32bytes.key"
        self.receipt = self.secrets/"immutable-root-receipt.json"
        self.dest = self.root/"NEW-synthetic-restore.sqlite3"
        create_key_file(self.key)

    def make(self, count=130):
        with SegmentedNoosphere(
            self.original, OWNER, segment_size=64,
        ) as source:
            tip = ZERO
            for tick in range(count):
                tip = source.append(record(tick), expected_head=tip)
            export_sealed_segments(
                source, self.copy_a, include_nonpublic=True,
                external_expected_head=tip,
            )
            capture_live_tail(
                source, self.copy_a, include_nonpublic=True,
                external_expected_source_head=tip,
            )
        statement = sign_owner_receipt(
            self.copy_a, self.receipt, self.key,
            source_events=count, external_expected_source_head=tip,
        )
        for member in self.copy_a.iterdir():
            shutil.copy2(member, self.copy_b/member.name)
        return tip, statement["receipt_sha256"]

    def recover(self, root_sha, *, key=None, agent=OWNER, dest=None,
                replica=None, allow=True):
        return recover_pinned_replica(
            self.copy_b if replica is None else replica,
            self.dest if dest is None else dest, self.receipt,
            self.key if key is None else key,
            independently_pinned_receipt_sha256=root_sha,
            expected_agent_id=agent, allow_private_restore=allow,
        )

    def test_original_sqlite_and_original_archive_absent_recover_full_130(self):
        head, digest = self.make()
        self.original.rename(self.root/"original-no-longer-available.sqlite3")
        self.copy_a.rename(self.root/"owner-primary-no-longer-available")
        result = self.recover(digest)
        self.assertEqual(result["restored_events"], 130)
        self.assertEqual(result["sealed_segments"], 2)
        self.assertEqual(result["restored_tail_events"], 2)
        self.assertEqual(result["restored_head"], head)
        self.assertEqual(result["custody"], "QUARANTINED_READ_ONLY")
        self.assertFalse(self.original.exists())
        self.assertFalse(self.copy_a.exists())
        with SegmentedNoosphere(self.dest, OWNER, segment_size=64) as q:
            self.assertEqual(q.verify_chain(external_expected_head=head)["count"], 130)
            self.assertEqual(q.event_at(130)["event"]["payload"]["sequence"], 129)
            self.assertEqual(q.dataset(limit=5)["projection"]["items"], [])
            before_count, before_head = q.count(), q.head()
            with self.assertRaises(NoosphereError):
                q.append(record(130), expected_head=before_head)
            self.assertEqual((q.count(), q.head()), (before_count, before_head))
            custody = q._db.execute(
                "SELECT state,pinned_source_head FROM recovery_custody WHERE id=1",
            ).fetchone()
            self.assertEqual(custody["state"], "QUARANTINED_READ_ONLY")
            self.assertEqual(custody["pinned_source_head"], head)
            with self.assertRaises(sqlite3.IntegrityError):
                q._db.execute(
                    "UPDATE recovery_custody SET state='OTHER' WHERE id=1"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                q._db.execute("DELETE FROM recovery_custody WHERE id=1")
            self.assertEqual(
                q._db.execute(
                    "SELECT count(*) FROM recovery_custody"
                ).fetchone()[0], 1,
            )
            self.assertEqual(q.count(), before_count)
            self.assertEqual(q.head(), before_head)

    def test_requires_explicit_private_consent_and_correct_actor(self):
        head, digest = self.make()
        with self.assertRaisesRegex(ReceiptError, "explicit"):
            self.recover(digest, allow=False)
        with self.assertRaisesRegex(ReceiptError, "different actor"):
            self.recover(digest, agent="other-actor")
        self.assertFalse(self.dest.exists())

    def test_wrong_key_or_external_digest_refuses_before_creating_db(self):
        _, digest = self.make()
        wrong_key = self.secrets/"unrelated-key.bin"
        create_key_file(wrong_key)
        with self.assertRaises(ReceiptError):
            self.recover(digest, key=wrong_key)
        with self.assertRaises(ReceiptError):
            self.recover("f"*64)
        self.assertFalse(self.dest.exists())

    def test_corrupted_replica_and_missing_tail_refuse_without_partial_restore(self):
        _, digest = self.make()
        data, _ = names(1)
        with (self.copy_b/data).open("ab") as stream:
            stream.write(b"altered")
        with self.assertRaises((ArchiveError, ReceiptError)):
            self.recover(digest)
        self.assertFalse(self.dest.exists())
        (self.copy_b/data).unlink()
        with self.assertRaises((ArchiveError, ReceiptError)):
            self.recover(digest)
        self.assertFalse(self.dest.exists())

    def test_no_inplace_restore_or_world_readable_destination(self):
        _, digest = self.make()
        with self.assertRaisesRegex(ReceiptError, "new and absent"):
            self.recover(digest, dest=self.original)
        with self.assertRaisesRegex(ReceiptError, "inside archive"):
            self.recover(digest, dest=self.copy_b/"restore.sqlite3")
        public = self.root/"world-readable"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(ArchiveError, "group/other"):
            self.recover(digest, dest=public/"new-recovery.sqlite3")
        self.assertEqual(list(public.iterdir()), [])
        self.assertFalse(self.dest.exists())

    def test_owner_key_and_root_receipt_cannot_be_bundled_with_replica(self):
        _, digest = self.make()
        embedded = self.copy_b/"forged-local-hmac.key"
        with self.assertRaisesRegex(ReceiptError, "inside replica"):
            self.recover(digest, key=embedded)
        self.assertFalse(self.dest.exists())
        embedded.unlink(missing_ok=True)
        with self.assertRaisesRegex(ReceiptError, "inside replica"):
            recover_pinned_replica(
                self.copy_b, self.dest,
                self.copy_b/"local-checkpoint.json", self.key,
                independently_pinned_receipt_sha256=digest,
                expected_agent_id=OWNER, allow_private_restore=True,
            )
        self.assertFalse(self.dest.exists())

    def test_idempotent_archive_source_does_not_overwrite_existing_restore(self):
        _, digest = self.make()
        first = self.recover(digest)
        self.assertEqual(first["restored_events"], 130)
        with self.assertRaisesRegex(ReceiptError, "new and absent"):
            self.recover(digest)
        with SegmentedNoosphere(self.dest, OWNER, segment_size=64) as q:
            self.assertEqual(q.count(), 130)

    def test_interruption_before_fenced_publish_leaves_no_visible_target(self):
        head, digest = self.make()
        import mojum.verified_replica_restore as subject
        actual_link = subject.os.link

        def stop_publishing(src, dest):
            if str(dest) == str(self.dest):
                raise RuntimeError("injected crash before public restored path")
            return actual_link(src, dest)

        with patch.object(subject.os, "link", side_effect=stop_publishing):
            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                self.recover(digest)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob(".mojum-verified-recovery-*")), [])
        with SegmentedNoosphere(self.original, OWNER, segment_size=64) as source:
            self.assertEqual(
                source.verify_chain(external_expected_head=head)["count"], 130,
            )

    def test_real_cpu_cli_cold_restore_from_replica_after_source_unavailable(self):
        head, digest = self.make()
        self.original.rename(self.root/"unavailable-source.sqlite3")
        self.copy_a.rename(self.root/"unavailable-first-archive")
        command = [
            sys.executable, "-m", "mojum.segment_archive_cli",
            "recover-replica",
            "--archive-dir", str(self.copy_b),
            "--new-db", str(self.dest),
            "--receipt-file", str(self.receipt),
            "--key-file", str(self.key),
            "--expected-receipt-sha", digest,
            "--expected-agent-id", OWNER,
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT/"src"))
        denied = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True,
            text=True, timeout=20,
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertFalse(self.dest.exists())
        allowed = subprocess.run(
            command + ["--include-private"], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        report = json.loads(allowed.stdout)
        self.assertEqual(report["restored_events"], 130)
        self.assertEqual(report["restored_head"], head)
        self.assertNotIn("PRIVATE_SYNTHETIC", allowed.stdout)
        self.assertNotIn(str(self.dest), allowed.stdout)
        self.assertNotIn(str(self.key), allowed.stdout)

    def test_actual_aquarium_checkpoint_replays_after_loss_of_source(self):
        # Independent end-to-end acceptance with original synthetic motor
        # state, not merely standalone journal rows.
        opts = dict(
            room=Room(.16,.16), start=(.16,.16),
            exploration_mode="goal_free", neural_mode="disabled",
            sensory_mode="none", developmental_policy="local_cell_v4",
            journal_backend="segmented", journal_segment_size=64,
        )
        with Aquarium(self.original, SEED, GRAPH, **opts) as organism:
            for _ in range(130):
                organism.step()
            before = organism.checkpoint()
            tip = before["journal_head"]
            count = organism.store.count()
            self.assertEqual(count, 131)
            export_sealed_segments(
                organism.store, self.copy_a, include_nonpublic=True,
                external_expected_head=tip,
            )
            capture_live_tail(
                organism.store, self.copy_a, include_nonpublic=True,
                external_expected_source_head=tip,
            )
        signed = sign_owner_receipt(
            self.copy_a, self.receipt, self.key, source_events=count,
            external_expected_source_head=tip,
        )
        for file in self.copy_a.iterdir():
            shutil.copy2(file, self.copy_b/file.name)
        self.original.rename(self.root/"original-unavailable.sqlite3")
        self.copy_a.rename(self.root/"first-archive-unavailable")
        recovered = self.recover(signed["receipt_sha256"])
        self.assertEqual(recovered["restored_events"], 131)
        with Aquarium(self.dest, SEED, GRAPH, **opts) as after:
            self.assertEqual(after.checkpoint(), before)
            # A restored historical copy must be inspectable but cannot
            # silently become another live writer on the same timeline.
            with self.assertRaises(NoosphereError):
                after.step()
            self.assertEqual(after.tick, 130)
            self.assertEqual(after.checkpoint(), before)


if __name__ == "__main__":
    unittest.main()
