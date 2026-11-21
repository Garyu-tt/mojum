"""Private synthetic Aquarium SQLite -> viewer JSONL acceptance tests.

All source data is created by the actual canonical Aquarium implementation,
not forged demo-frame objects. Verify source bytes, replay, privacy and guards.
"""
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_trace_export import export_private_trace, SCHEMA
from mojum.noosphere import NoosphereError
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class PrivateAquariumTraceExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "one-organism.sqlite3"
        self.out = self.root / "private-trace.jsonl"

    def aquarium(self, **kw):
        return Aquarium(self.db, SEED, GRAPH, **kw)

    def exported(self, **options):
        options.setdefault("allow_private_export", True)
        return export_private_trace(
            self.db, self.out, SEED, GRAPH, **options,
        )

    def frames_and_summary(self):
        rows = [json.loads(line) for line in self.out.read_text(encoding="utf-8").splitlines()]
        return rows[:-1], rows[-1]

    def test_replays_real_private_frames_in_same_tick_order(self):
        with self.aquarium() as a:
            original = [a.step(dx=.025) for _ in range(8)]
            head = a.store.head()
            count = a.store.count()
        before = self.db.read_bytes()
        result = self.exported(expected_journal_head=head)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["frames"], 8)
        self.assertEqual(result["event_count"], count)
        self.assertEqual(result["journal_head"], head)
        self.assertEqual(result["classification"], "PRIVATE_SYNTHETIC_REPLAY_NOT_AUTHENTICATED")
        self.assertEqual(result["trace_sha256"], hashlib.sha256(self.out.read_bytes()).hexdigest())
        frames, end = self.frames_and_summary()
        self.assertEqual(frames, original)
        self.assertEqual([f["tick"] for f in frames], list(range(8)))
        self.assertEqual(end["schema"], "mojum.aquarium.checkpoint/1")
        self.assertEqual(end["stored_events"], 9)
        self.assertEqual(end["journal_head"], head)
        self.assertNotIn("neural_state", end)
        self.assertNotIn("genome_revision", end)
        self.assertTrue(all(f["brain"]["status"] == "UNIMPLEMENTED" for f in frames))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o600)

    def test_source_open_with_uncheckpointed_wal_remains_unchanged(self):
        with self.aquarium() as running:
            rows = [running.step(dx=0.01) for _ in range(4)]
            source_bytes = self.db.read_bytes()
            before = running.checkpoint()
            self.assertTrue(Path(str(self.db) + "-wal").exists())
            result = self.exported(expected_journal_head=before["journal_head"])
            self.assertEqual(self.db.read_bytes(), source_bytes)
            self.assertEqual(running.checkpoint(), before)
            self.assertEqual(result["frames"], 4)
            self.assertEqual(self.frames_and_summary()[0], rows)
            # Export does not acquire the writer identity or append an event.
            self.assertEqual(running.store.count(), 5)
            self.assertEqual(running.step()["tick"], 4)

    def test_existing_destination_is_never_overwritten(self):
        with self.aquarium() as a:
            a.step()
        self.out.write_text("DO NOT MODIFY", encoding="utf-8")
        before = self.out.read_bytes()
        with self.assertRaisesRegex(ValueError, "new filename"):
            self.exported()
        self.assertEqual(self.out.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.root.glob(".mojum-private-trace-*")), [])

    def test_private_export_requires_explicit_opt_in(self):
        with self.aquarium() as a:
            a.step()
        with self.assertRaises(PermissionError):
            self.exported(allow_private_export=False)
        self.assertFalse(self.out.exists())

    def test_independently_expected_head_is_enforced(self):
        with self.aquarium() as a:
            a.step()
            head = a.store.head()
        for bad in ("0" * 64, "z" * 64, True, 0, "ab"):
            with self.subTest(head=bad), self.assertRaises((NoosphereError, ValueError)):
                self.exported(expected_journal_head=bad)
            self.assertFalse(self.out.exists())
        self.assertEqual(self.exported(expected_journal_head=head)["journal_head"], head)

    def test_bad_seed_or_room_rejected_before_output_publication(self):
        with self.aquarium() as a:
            a.step()
        bad = copy.deepcopy(SEED)
        bad["inherited"]["heartbeat_bpm"] = 100
        with self.assertRaises(AquariumError):
            export_private_trace(
                self.db, self.out, bad, GRAPH, allow_private_export=True,
            )
        self.assertFalse(self.out.exists())
        with self.assertRaises(AquariumError):
            self.exported(room=Room(5, 5))
        self.assertFalse(self.out.exists())

    def test_empty_sqlite_and_unrecognized_file_do_not_bootstrap_source(self):
        with closing(sqlite3.connect(self.db)) as db:
            with db:
                db.execute("CREATE TABLE sentinel (value TEXT)")
                db.execute("INSERT INTO sentinel VALUES ('untouched')")
        before = self.db.read_bytes()
        with self.assertRaises(ValueError):
            self.exported()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(self.out.exists())
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute(
                "SELECT value FROM sentinel").fetchone()[0], "untouched")

    def test_missing_source_and_symlink_fail_without_side_effect(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.exported()
        self.assertFalse(self.out.exists())
        with self.aquarium() as a:
            a.step()
        if hasattr(os, "symlink"):
            other = self.root / "shortcut.sqlite3"
            other.symlink_to(self.db)
            with self.assertRaisesRegex(ValueError, "symlink"):
                export_private_trace(
                    other, self.out, SEED, GRAPH, allow_private_export=True)
            self.assertFalse(self.out.exists())

    def test_trace_size_limit_fail_closed_and_no_partial_file(self):
        with self.aquarium() as a:
            a.step()
        with patch("mojum.aquarium_trace_export.MAX_TRACE_BYTES", 5):
            with self.assertRaisesRegex(ValueError, "24 MiB"):
                self.exported()
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".mojum-private-trace-*")), [])

    def test_zero_frame_bootstrap_only_rejected_without_modifying_db(self):
        with self.aquarium() as a:
            self.assertEqual(a.store.count(), 1)
        before = self.db.read_bytes()
        with self.assertRaisesRegex(AquariumError, "nonempty"):
            self.exported()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(self.out.exists())

    def test_changed_committed_history_fail_closed(self):
        with self.aquarium() as a:
            a.step()
        with closing(sqlite3.connect(self.db)) as connection:
            with connection:
                connection.execute("DROP TRIGGER noosphere_no_update")
                connection.execute("UPDATE events SET event_hash=? WHERE seq=2",
                                   ("f" * 64,))
        with self.assertRaises(NoosphereError):
            self.exported()
        self.assertFalse(self.out.exists())

    def test_cli_exports_to_file_without_leaking_private_payload_to_stdout(self):
        with self.aquarium() as a:
            for _ in range(2):
                a.step()
            head = a.store.head()
        cmd = [
            sys.executable, "-m", "mojum.aquarium_trace_export",
            "--db", str(self.db), "--output", str(self.out),
            "--expected-head", head,
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        refused = subprocess.run(
            cmd, env=env, cwd=ROOT, capture_output=True, text=True,
            timeout=30, check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertFalse(self.out.exists())
        done = subprocess.run(
            cmd + ["--allow-private-export"], env=env, cwd=ROOT,
            capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual(result["frames"], 2)
        self.assertEqual(result["journal_head"], head)
        self.assertNotIn("interoception", done.stdout)
        self.assertNotIn("phenotype", done.stdout)
        self.assertNotIn("movement_request", done.stdout)
        self.assertEqual(len(self.frames_and_summary()[0]), 2)
        self.assertFalse(list(self.root.glob(".mojum-private-trace-*")))


if __name__ == "__main__":
    unittest.main()
