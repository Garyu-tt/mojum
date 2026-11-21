"""R0.17 physical sealed archives: independent recovery, corruption, privacy.

All data are synthetic, created under temporary directories, never copied
from actual user PRIVATE Noosphere or touched on production/Android.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.noosphere import NoosphereError
from mojum.segmented_noosphere import SegmentedNoosphere
from mojum.segment_archive import (
    ArchiveError, export_sealed_segments, names,
)
from mojum.segment_archive_verify import (
    verify_sealed_archive, restore_into_new_sqlite,
)
from test_segmented_noosphere import OWNER, ZERO, record


class PhysicalArchiveUnitQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "PRIVATE-synthetic.sqlite3"
        self.archive = self.root / "archive-a"
        self.archive.mkdir(mode=0o700)
        self.copy = self.root / "archive-b"
        self.copy.mkdir(mode=0o700)
        self.recovered = self.root / "recovered.sqlite3"

    def build(self, n=130):
        with SegmentedNoosphere(
            self.source, OWNER, segment_size=64,
        ) as journal:
            head = ZERO
            for tick in range(n):
                head = journal.append(record(tick), expected_head=head)
            return head

    def export(self, folder=None, **extra):
        with SegmentedNoosphere(
            self.source, OWNER, segment_size=64,
        ) as journal:
            return export_sealed_segments(
                journal, folder or self.archive,
                include_nonpublic=True, **extra,
            )

    def test_130_events_archive_128_only_then_offline_recover(self):
        head = self.build(130)
        snapshot = self.export(external_expected_head=head)
        self.assertEqual(snapshot["sealed_segments"], 2)
        self.assertEqual(snapshot["archived_events"], 128)
        self.assertEqual(snapshot["unsealed_source_events"], 2)
        self.assertNotEqual(snapshot["sealed_head"], head)
        proven = verify_sealed_archive(
            self.archive, external_expected_head=snapshot["sealed_head"],
        )
        self.assertEqual(proven["sealed_segments"], 2)
        self.assertEqual(proven["archived_events"], 128)
        self.assertTrue(proven["contains_private"])
        self.assertEqual(self.export()["copied_segments"], 0)

        # Simulate the ONLY source being unavailable. This is a test fixture
        # rename, NOT a destructive operation on a real user database.
        self.source.rename(self.root / "source-offline.sqlite3")
        result = restore_into_new_sqlite(
            self.archive, self.recovered,
            external_expected_head=snapshot["sealed_head"],
        )
        self.assertEqual(result["restored_events"], 128)
        self.assertFalse(self.source.exists())
        with SegmentedNoosphere(
            self.recovered, OWNER, segment_size=64,
        ) as recovered:
            verified = recovered.verify_chain(
                external_expected_head=snapshot["sealed_head"],
            )
            self.assertEqual(verified["segments"], 2)
            self.assertEqual(verified["count"], 128)
            self.assertEqual(
                recovered.event_at(128)["event"]["payload"]["sequence"], 127,
            )
        # Do NOT pretend unsealed tail seq129..130 was backed up.
        with self.assertRaises(ArchiveError):
            verify_sealed_archive(
                self.archive, external_expected_head=head,
            )

    def test_two_explicit_copies_byte_identical_but_same_failure_domain(self):
        self.build(192)
        first = self.export()
        second = self.export(self.copy)
        self.assertEqual(first["sealed_segments"], 3)
        self.assertEqual(second["archived_events"], 192)
        for idx in range(3):
            for name in names(idx):
                self.assertEqual(
                    (self.archive / name).read_bytes(),
                    (self.copy / name).read_bytes(),
                )
        for root in (self.archive, self.copy):
            v = verify_sealed_archive(
                root, external_expected_head=first["sealed_head"],
            )
            self.assertEqual(v["archived_events"], 192)
        # Two directories on one tmp volume are NOT two independent replicas.

    def test_private_never_exported_by_default_or_to_public_folder(self):
        self.build(64)
        with SegmentedNoosphere(self.source, OWNER, segment_size=64) as q:
            with self.assertRaisesRegex(ArchiveError, "explicit"):
                export_sealed_segments(q, self.archive)
            self.assertEqual(list(self.archive.iterdir()), [])
            exposed = self.root / "world-readable"
            exposed.mkdir(mode=0o755)
            with self.assertRaisesRegex(ArchiveError, "group/other"):
                export_sealed_segments(
                    q, exposed, include_nonpublic=True,
                )
            self.assertFalse(list(exposed.iterdir()))

    def test_private_restore_into_public_directory_is_denied(self):
        self.build(64)
        receipt = self.export()
        public = self.root / "world-readable-restore"
        public.mkdir(mode=0o755)
        target = public / "PRIVATE-synthetic-recovered.sqlite3"
        with self.assertRaisesRegex(ArchiveError, "group/other"):
            restore_into_new_sqlite(
                self.archive, target,
                external_expected_head=receipt["sealed_head"],
            )
        self.assertFalse(target.exists())
        self.assertEqual(list(public.iterdir()), [])

    def test_data_corruption_and_missing_manifest_are_rejected(self):
        self.build(128)
        receipt = self.export()
        data, manifest = names(1)
        with (self.archive / data).open("ab") as stream:
            stream.write(b"untrusted-data")
        with self.assertRaises(ArchiveError):
            verify_sealed_archive(
                self.archive, external_expected_head=receipt["sealed_head"],
            )
        with self.assertRaises(ArchiveError):
            restore_into_new_sqlite(
                self.archive, self.recovered,
                external_expected_head=receipt["sealed_head"],
            )
        self.assertFalse(self.recovered.exists())
        (self.archive / data).unlink()
        with self.assertRaises(ArchiveError):
            verify_sealed_archive(
                self.archive, external_expected_head=receipt["sealed_head"],
            )

    def test_invalid_external_head_cannot_publish_restored_source(self):
        self.build(128)
        receipt = self.export()
        with self.assertRaisesRegex(ArchiveError, "trusted anchor"):
            restore_into_new_sqlite(
                self.archive, self.recovered,
                external_expected_head="f" * 64,
            )
        self.assertFalse(self.recovered.exists())
        with self.assertRaises(ArchiveError):
            restore_into_new_sqlite(
                self.archive, self.recovered,
                external_expected_head="invalid",
            )
        self.assertFalse(self.recovered.exists())

    def test_interruption_after_data_before_manifest_fails_closed(self):
        self.build(64)
        import mojum.segment_archive as archive_mod
        true_publish = archive_mod.publish_new
        calls = []

        def interrupted(path, blocks):
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError("injected interruption before manifest")
            return true_publish(path, blocks)

        with patch.object(archive_mod, "publish_new", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.export()
        data, manifest = names(0)
        self.assertTrue((self.archive / data).is_file())
        self.assertFalse((self.archive / manifest).exists())
        with self.assertRaisesRegex(ArchiveError, "incomplete"):
            self.export()
        with self.assertRaises(ArchiveError):
            verify_sealed_archive(
                self.archive,
                external_expected_head="f" * 64,
            )

    def test_existing_destination_and_cross_source_mismatch_fail_closed(self):
        self.build(64)
        receipt = self.export()
        self.recovered.write_text("KEEP-ME", encoding="utf-8")
        with self.assertRaisesRegex(ArchiveError, "new absent"):
            restore_into_new_sqlite(
                self.archive, self.recovered,
                external_expected_head=receipt["sealed_head"],
            )
        self.assertEqual(self.recovered.read_text(), "KEEP-ME")

        # Reuse an existing archive path with an unrelated source of the
        # same actor, same seq but different canonical event content.
        other = self.root / "unrelated.sqlite3"
        with SegmentedNoosphere(other, OWNER, segment_size=64) as log:
            head = ZERO
            for i in range(64):
                event = record(i)
                event["payload"]["classification"] = "DIFFERENT-SOURCE"
                head = log.append(event, expected_head=head)
            with self.assertRaises(ArchiveError):
                export_sealed_segments(
                    log, self.archive, include_nonpublic=True,
                )

    def test_real_offline_cli_export_verify_restore_no_private_stdout(self):
        # Explicit CLI proof: no external connector and no live user data.
        source_head = self.build(64)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        root = Path(__file__).resolve().parents[1]

        def call(*arguments):
            return subprocess.run(
                [sys.executable, "-m", "mojum.segment_archive_cli",
                 *map(str, arguments)],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=20,
            )

        base = (
            "export", "--db", self.source,
            "--agent-id", OWNER, "--segment-size", "64",
            "--archive-dir", self.archive,
            "--expected-source-head", source_head,
        )
        denied = call(*base)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(list(self.archive.iterdir()), [])
        allowed = call(*base, "--include-private")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        summary = json.loads(allowed.stdout)
        self.assertEqual(summary["archived_events"], 64)
        self.assertNotIn("PRIVATE_SYNTHETIC", allowed.stdout)
        sealed_head = summary["sealed_head"]
        self.assertEqual(sealed_head, source_head)
        self.source.rename(self.root / "synthetic-source-offline.sqlite3")
        verify = call(
            "verify", "--archive-dir", self.archive,
            "--expected-sealed-head", sealed_head,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout)["archived_events"], 64)
        restore = call(
            "restore", "--archive-dir", self.archive,
            "--new-db", self.recovered,
            "--expected-sealed-head", sealed_head,
        )
        self.assertEqual(restore.returncode, 0, restore.stderr)
        self.assertEqual(json.loads(restore.stdout)["restored_events"], 64)
        self.assertNotIn(str(self.recovered), restore.stdout)
        with SegmentedNoosphere(
            self.recovered, OWNER, segment_size=64,
        ) as q:
            self.assertEqual(
                q.verify_chain(external_expected_head=sealed_head)["count"], 64,
            )

    def test_symlink_archive_file_rejected(self):
        self.build(64)
        receipt = self.export()
        data, _ = names(0)
        file = self.archive / data
        copy = self.root / "symlink-target"
        copy.write_bytes(file.read_bytes())
        file.unlink()
        try:
            file.symlink_to(copy)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks unavailable on this test filesystem")
        with self.assertRaisesRegex(ArchiveError, "symlink"):
            verify_sealed_archive(
                self.archive, external_expected_head=receipt["sealed_head"],
            )


if __name__ == "__main__":
    unittest.main()
