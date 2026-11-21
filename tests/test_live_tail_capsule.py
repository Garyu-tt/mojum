"""R0.18 immutable live-tail point-in-time backup / independent cold restore.

Only disposable synthetic owner-only SQLite and archive files. No real
PRIVATE Noosphere, physical device, server volume or production mutation.
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
from mojum.live_tail_capsule import (
    capture_live_tail, verify_point_in_time, restore_point_in_time,
    tail_names,
)
from mojum.aquarium import Aquarium
from mojum.world_room import Room
from test_segmented_noosphere import OWNER, ZERO, record


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8")), GRAPH, SEED


class SyntheticTailArchiveQA(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "PRIVATE-synthetic-source.sqlite3"
        self.folder = self.root / "PRIVATE-archive"
        self.folder.mkdir(mode=0o700)
        self.restored = self.root / "restored-synthetic.sqlite3"

    def make(self, n=130):
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as journal:
            tip = ZERO
            for i in range(n):
                tip = journal.append(record(i), expected_head=tip)
            return tip

    def backup(self, pinned):
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as source:
            sealed = export_sealed_segments(
                source, self.folder, include_nonpublic=True,
                external_expected_head=pinned,
            )
            tail = capture_live_tail(
                source, self.folder, include_nonpublic=True,
                external_expected_source_head=pinned,
            )
        return sealed, tail

    def test_130_total_complete_recovery_of_128_sealed_plus_2_tail(self):
        head = self.make(130)
        sealed, tail = self.backup(head)
        self.assertEqual(sealed["archived_events"], 128)
        self.assertEqual(sealed["unsealed_source_events"], 2)
        self.assertEqual(tail["archived_events"], 130)
        self.assertEqual(tail["tail_events"], 2)
        self.assertEqual(tail["sealed_segments"], 2)
        self.assertEqual(tail["source_head"], head)
        self.assertEqual(
            capture_live_tail_again(self.db, self.folder, head)["copied"],
            False,
        )
        info = verify_point_in_time(
            self.folder, source_events=130,
            external_expected_source_head=head,
        )
        self.assertEqual(info["archived_events"], 130)
        self.assertEqual(info["tail_events"], 2)
        self.db.rename(self.root / "source-disconnected.sqlite3")
        recovered = restore_point_in_time(
            self.folder, self.restored, source_events=130,
            external_expected_source_head=head,
        )
        self.assertEqual(recovered["restored_events"], 130)
        self.assertEqual(recovered["restored_tail_events"], 2)
        with SegmentedNoosphere(self.restored, OWNER, segment_size=64) as again:
            self.assertEqual(
                again.verify_chain(external_expected_head=head)["count"], 130,
            )
            self.assertEqual(again.event_at(130)["event"]["payload"]["sequence"], 129)
        self.assertFalse(self.db.exists())

    def test_genesis_only_tail_7_events_can_restore_without_sealed_prefix(self):
        head = self.make(7)
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as source:
            capsule = capture_live_tail(
                source, self.folder, include_nonpublic=True,
                external_expected_source_head=head,
            )
            self.assertEqual(capsule["sealed_segments"], 0)
            self.assertEqual(capsule["tail_events"], 7)
        self.db.rename(self.root / "disconnected.sqlite3")
        again = restore_point_in_time(
            self.folder, self.restored, source_events=7,
            external_expected_source_head=head,
        )
        self.assertEqual(again["restored_events"], 7)
        with SegmentedNoosphere(self.restored, OWNER, segment_size=64) as q:
            self.assertEqual(q.verify_chain(external_expected_head=head)["count"], 7)

    def test_public_tail_does_not_hide_private_sealed_history(self):
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as q:
            tip = ZERO
            for i in range(65):
                tip = q.append(
                    record(i, visibility="PUBLIC" if i == 64 else "PRIVATE"),
                    expected_head=tip,
                )
            export_sealed_segments(
                q, self.folder, include_nonpublic=True,
                external_expected_head=tip,
            )
            captured = capture_live_tail(
                q, self.folder, include_nonpublic=True,
                external_expected_source_head=tip,
            )
        self.assertEqual(captured["tail_events"], 1)
        self.assertTrue(captured["contains_private"])
        checked = verify_point_in_time(
            self.folder, source_events=65,
            external_expected_source_head=tip,
        )
        self.assertTrue(checked["contains_private"])

    def test_source_can_advance_but_old_point_in_time_remains_explicit(self):
        original = self.make(65)
        self.backup(original)
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as source:
            source.append(record(65), expected_head=source.head())
            current = source.head()
        self.assertNotEqual(original, current)
        verified = verify_point_in_time(
            self.folder, source_events=65,
            external_expected_source_head=original,
        )
        self.assertEqual(verified["source_head"], original)
        with self.assertRaises(ArchiveError):
            verify_point_in_time(
                self.folder, source_events=65,
                external_expected_source_head=current,
            )
        with self.assertRaises(ArchiveError):
            verify_point_in_time(
                self.folder, source_events=66,
                external_expected_source_head=current,
            )

    def test_corruption_and_external_anchor_mismatch_fail_before_target(self):
        head = self.make(129)
        self.backup(head)
        with self.assertRaises(ArchiveError):
            restore_point_in_time(
                self.folder, self.restored, source_events=129,
                external_expected_source_head="e"*64,
            )
        self.assertFalse(self.restored.exists())
        data, _ = tail_names(129, 129)
        with (self.folder / data).open("ab") as out:
            out.write(b"\nCORRUPTED")
        with self.assertRaises(ArchiveError):
            restore_point_in_time(
                self.folder, self.restored, source_events=129,
                external_expected_source_head=head,
            )
        self.assertFalse(self.restored.exists())

    def test_explicit_private_export_and_public_destination_refused(self):
        head = self.make(3)
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as source:
            with self.assertRaisesRegex(ArchiveError, "include_nonpublic"):
                capture_live_tail(
                    source, self.folder,
                    external_expected_source_head=head,
                )
            self.assertEqual(list(self.folder.iterdir()), [])
            public = self.root / "public"
            public.mkdir(mode=0o755)
            with self.assertRaisesRegex(ArchiveError, "group/other"):
                capture_live_tail(
                    source, public, include_nonpublic=True,
                    external_expected_source_head=head,
                )
            self.assertEqual(list(public.iterdir()), [])

    def test_crash_after_tail_data_before_manifest_quarantines_orphan(self):
        head = self.make(7)
        import mojum.live_tail_capsule as module
        original_publish = module.publish_new
        calls = []

        def crash_second(path, blocks):
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError("simulated interruption before manifest")
            return original_publish(path, blocks)

        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as source:
            with patch.object(module, "publish_new", side_effect=crash_second):
                with self.assertRaisesRegex(RuntimeError, "interruption"):
                    capture_live_tail(
                        source, self.folder, include_nonpublic=True,
                        external_expected_source_head=head,
                    )
            data, manifest = tail_names(1, 7)
            self.assertTrue((self.folder/data).exists())
            self.assertFalse((self.folder/manifest).exists())
            with self.assertRaisesRegex(ArchiveError, "orphan"):
                capture_live_tail(
                    source, self.folder, include_nonpublic=True,
                    external_expected_source_head=head,
                )
        self.assertFalse(self.restored.exists())

    def test_sealed_prefix_missing_or_wrong_size_is_not_hidden_by_tail(self):
        head = self.make(130)
        self.backup(head)
        data, _ = names(0)
        (self.folder/data).unlink()
        with self.assertRaises(ArchiveError):
            verify_point_in_time(
                self.folder, source_events=130,
                external_expected_source_head=head,
            )

    def test_explicit_cpu_cli_backups_unsealed_tail_no_private_stdout(self):
        head = self.make(130)
        # Seal the 128-event prefix explicitly; the CLI never exports
        # the PRIVATE tail or additional history without a separate flag.
        with SegmentedNoosphere(self.db, OWNER, segment_size=64) as q:
            export_sealed_segments(
                q, self.folder, include_nonpublic=True,
                external_expected_head=head,
            )
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))

        def run(*args):
            return subprocess.run([
                sys.executable, "-m", "mojum.segment_archive_cli",
                *map(str, args),
            ], env=env, cwd=ROOT, text=True, capture_output=True,
               timeout=20)

        common = (
            "capture-tail", "--db", self.db, "--agent-id", OWNER,
            "--segment-size", "64", "--archive-dir", self.folder,
            "--expected-source-head", head,
        )
        denied = run(*common)
        self.assertNotEqual(denied.returncode, 0)
        self.assertNotIn("PRIVATE_SYNTHETIC", denied.stdout)
        accepted = run(*common, "--include-private")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        report = json.loads(accepted.stdout)
        self.assertEqual(report["tail_events"], 2)
        self.assertEqual(report["archived_events"], 130)
        self.assertNotIn("PRIVATE_SYNTHETIC", accepted.stdout)
        verify = run(
            "verify-point", "--archive-dir", self.folder,
            "--source-events", "130", "--expected-source-head", head,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout)["source_head"], head)
        self.db.rename(self.root/"disconnected-source.sqlite3")
        restore = run(
            "restore-point", "--archive-dir", self.folder,
            "--new-db", self.restored, "--source-events", "130",
            "--expected-source-head", head,
        )
        self.assertEqual(restore.returncode, 0, restore.stderr)
        self.assertEqual(json.loads(restore.stdout)["restored_events"], 130)
        self.assertNotIn(str(self.restored), restore.stdout)
        self.assertNotIn("PRIVATE_SYNTHETIC", restore.stdout)
        with SegmentedNoosphere(self.restored, OWNER, segment_size=64) as q:
            self.assertEqual(q.verify_chain(external_expected_head=head)["count"], 130)

    def test_new_restore_path_only_and_no_overwrite(self):
        head = self.make(65)
        self.backup(head)
        self.restored.write_text("DO-NOT-OVERWRITE", encoding="utf-8")
        with self.assertRaises(ArchiveError):
            restore_point_in_time(
                self.folder, self.restored, source_events=65,
                external_expected_source_head=head,
            )
        self.assertEqual(self.restored.read_text(), "DO-NOT-OVERWRITE")


class AquariumTailRecoveryQA(unittest.TestCase):
    def test_real_130_tick_v4_aquarium_cold_replays_after_source_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "PRIVATE-original.sqlite3"
            output = root / "PRIVATE-restored.sqlite3"
            archive = root / "private-segments"
            archive.mkdir(mode=0o700)
            opts = {
                "room": Room(.16,.16), "start": (.16,.16),
                "exploration_mode": "goal_free",
                "neural_mode": "disabled", "sensory_mode": "none",
                "developmental_policy": "local_cell_v4",
                "journal_backend": "segmented",
                "journal_segment_size": 64,
            }
            with Aquarium(src, SEED, GRAPH, **opts) as aquarium:
                for _ in range(130):
                    aquarium.step()
                before = aquarium.checkpoint()
                old_head = before["journal_head"]
                self.assertEqual(aquarium.store.count(), 131)
                export_sealed_segments(
                    aquarium.store, archive, include_nonpublic=True,
                    external_expected_head=old_head,
                )
                snapshot = capture_live_tail(
                    aquarium.store, archive, include_nonpublic=True,
                    external_expected_source_head=old_head,
                )
                self.assertEqual(snapshot["sealed_segments"], 2)
                self.assertEqual(snapshot["tail_events"], 3)
            src.rename(root / "unavailable-original.sqlite3")
            restored = restore_point_in_time(
                archive, output, source_events=131,
                external_expected_source_head=old_head,
            )
            self.assertEqual(restored["restored_events"], 131)
            with Aquarium(output, SEED, GRAPH, **opts) as aquarium:
                self.assertEqual(aquarium.checkpoint(), before)
                self.assertEqual(aquarium.store.head(), old_head)
                aquarium.step()
                self.assertEqual(aquarium.tick, 131)


def capture_live_tail_again(db, folder, head):
    with SegmentedNoosphere(db, OWNER, segment_size=64) as q:
        return capture_live_tail(
            q, folder, include_nonpublic=True,
            external_expected_source_head=head,
        )


if __name__ == "__main__":
    unittest.main()
