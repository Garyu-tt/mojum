"""Cold-copy real synthetic Aquarium journals, including WAL and GENESIS replay."""

from contextlib import closing
import copy
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_cold_copy import export_cold_copy, verify_cold_copy
from mojum.genesis import ACQUISITION_SCHEMA


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class ColdAquariumCopyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.sqlite3"
        self.exported = self.root / "exported.sqlite3"

    def _export(self):
        return export_cold_copy(self.source, self.exported, SEED, GRAPH)

    def _verify(self, metadata, **extra):
        args = dict(expected_agent_id=metadata["agent_id"],
                    expected_journal_head=metadata["journal_head"],
                    expected_sha256=metadata["db_sha256"],
                    expected_tick=metadata["tick"])
        args.update(extra)
        return verify_cold_copy(self.exported, SEED, GRAPH, **args)

    def test_real_journal_roundtrip_and_independent_resume(self):
        with Aquarium(self.source, SEED, GRAPH) as source:
            for _ in range(9):
                source.step(dx=0.02)
            baseline = source.checkpoint()
            exported = self._export()  # source is still open in WAL mode
            self.assertEqual(exported["journal_head"], baseline["journal_head"])
            self.assertEqual(exported["tick"], baseline["tick"])
            self.assertEqual(exported["event_count"], 10)
            self.assertEqual(self._verify(exported)["tick"], 9)
            # A second isolated process may resume the verified copy. This
            # does NOT mean both databases form a replicated organism.
            with Aquarium(self.exported, SEED, GRAPH,
                          external_expected_head=exported["journal_head"]) as imported:
                self.assertEqual(imported.checkpoint(), baseline)
                imported.step(dx=0.04)
                self.assertEqual(imported.tick, 10)
            self.assertEqual(source.checkpoint(), baseline)
            source.step(dx=0.0)
            self.assertEqual(source.tick, 10)

    def test_acquired_genome_and_shadow_neural_state_replay(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            child.step(dx=0)
            child.step(dx=0)
            acquisition = {
                "schema": ACQUISITION_SCHEMA, "event_id": "learned-fixture",
                "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm",
                "delta": 15, "valid_from_tick": 2,
                "observed_at_ns": 0, "recorded_at_ns": 20_000_000,
                "source": "synthetic-unit-test", "evidence": "not real learning",
            }
            child.acquire(acquisition)
            for _ in range(8):
                child.step(dx=0)
            checkpoint = copy.deepcopy(child.checkpoint())
        metadata = self._export()
        self.assertEqual(metadata["event_count"], 12)
        self.assertEqual(self._verify(metadata)["event_count"], 12)
        with Aquarium(self.exported, SEED, GRAPH) as resumed:
            self.assertEqual(resumed.checkpoint(), checkpoint)
            self.assertEqual(resumed.genome.phenotype(10)["parameters"]["heartbeat_bpm"], 75)
            self.assertEqual(resumed.learner.snapshot(), checkpoint["neural_state"])

    def test_wrong_identity_head_tick_and_digest_fail_closed(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            for _ in range(3):
                child.step()
        metadata = self._export()
        cases = (
            {"expected_agent_id": "stranger"},
            {"expected_journal_head": "0" * 64},
            {"expected_tick": 4},
            {"expected_sha256": "f" * 64},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises((ValueError, AquariumError)):
                self._verify(metadata, **case)

    def test_input_validation_rejects_incorrect_anchor_types(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            child.step()
        metadata = self._export()
        for bad in (None, True, 42, "", "A" * 64, "a" * 63):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self._verify(metadata, expected_journal_head=bad)

    def test_no_clobber_when_backup_filename_exists(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            child.step()
        first = self._export()
        old_bytes = self.exported.read_bytes()
        with self.assertRaises(FileExistsError):
            self._export()
        self.assertEqual(self.exported.read_bytes(), old_bytes)
        self.assertEqual(self._verify(first)["journal_head"], first["journal_head"])
        self.assertFalse(list(self.root.glob(".mojum-cold-copy-*")))

    def test_existing_empty_sqlite_source_refused_without_bootstrap(self):
        # Export must never turn an unrelated existing SQLite into an
        # Aquarium journal merely by opening it for a backup.
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE TABLE unrelated_data (value TEXT)")
            db.execute("INSERT INTO unrelated_data VALUES ('keep-me')")
            db.commit()
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "existing nonempty Aquarium journal"):
            self._export()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(self.exported.exists())
        self.assertFalse(Path(str(self.source) + "-wal").exists())
        self.assertFalse(Path(str(self.source) + "-shm").exists())

    def test_export_rejects_uninitialized_source_without_mutating_bytes(self):
        # An existing path is not evidence of an Aquarium journal.
        # A writable Aquarium constructor otherwise bootstraps or repairs it.
        for kind in ("empty_file", "unrelated_sqlite", "events_without_metadata",
                     "events_with_empty_metadata"):
            with self.subTest(kind=kind):
                candidate = self.root / (kind + ".sqlite3")
                if kind == "empty_file":
                    candidate.touch()
                else:
                    with closing(sqlite3.connect(candidate)) as db:
                        if kind == "unrelated_sqlite":
                            db.execute("CREATE TABLE unrelated (x INTEGER)")
                            db.execute("INSERT INTO unrelated VALUES (99)")
                        else:
                            db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, wire_json TEXT)")
                            db.execute("INSERT INTO events VALUES (1, '{}')")
                            if kind == "events_with_empty_metadata":
                                db.execute("CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT)")
                        db.commit()
                before = candidate.read_bytes()
                with self.assertRaises(ValueError):
                    export_cold_copy(candidate, self.exported, SEED, GRAPH)
                self.assertEqual(candidate.read_bytes(), before)
                self.assertFalse(self.exported.exists())
                self.assertFalse(list(self.root.glob(".mojum-cold-copy-*")))

    def test_missing_source_does_not_create_new_database(self):
        with self.assertRaises(ValueError):
            self._export()
        self.assertFalse(self.source.exists())
        self.assertFalse(self.exported.exists())

    def test_corruption_refused_and_source_unchanged(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            child.step()
        metadata = self._export()
        original = self.source.read_bytes()
        with self.exported.open("r+b") as out:
            out.seek(0)
            out.write(b"NOT_SQLITE_JOURNAL")
        with self.assertRaises(ValueError):
            self._verify(metadata)
        self.assertEqual(self.source.read_bytes(), original)

    def test_failure_after_backup_does_not_publish_partial_file(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            child.step()
        with patch("mojum.aquarium_cold_copy._sha256", side_effect=ValueError("fail hash")):
            with self.assertRaisesRegex(ValueError, "fail hash"):
                self._export()
        self.assertFalse(self.exported.exists())
        self.assertFalse(list(self.root.glob(".mojum-cold-copy-*")))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_source_and_destination_rejected(self):
        with Aquarium(self.source, SEED, GRAPH) as child:
            child.step()
        symlink = self.root / "source-link"
        try:
            symlink.symlink_to(self.source)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        with self.assertRaises(ValueError):
            export_cold_copy(symlink, self.exported, SEED, GRAPH)
        protected = self.root / "protected"
        protected.write_bytes(b"protected-data")
        self.exported.symlink_to(protected)
        with self.assertRaises(FileExistsError):
            self._export()
        self.assertEqual(protected.read_bytes(), b"protected-data")

    def test_cli_export_then_verify_in_two_processes(self):
        import subprocess
        import sys

        with Aquarium(self.source, SEED, GRAPH) as child:
            for _ in range(6):
                child.step()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        prefix = [sys.executable, "-m", "mojum.aquarium_cold_copy"]
        exported = json.loads(subprocess.check_output(
            [*prefix, "export", "--db", str(self.source),
             "--output", str(self.exported)], text=True, env=env))
        verified = json.loads(subprocess.check_output(
            [*prefix, "verify", "--db", str(self.exported),
             "--expected-head", exported["journal_head"],
             "--expected-sha256", exported["db_sha256"],
             "--expected-tick", "6"], text=True, env=env))
        self.assertEqual(exported["tick"], verified["tick"])
        self.assertEqual(exported["journal_head"], verified["journal_head"])
        self.assertEqual(verified["event_count"], 7)
        self.assertNotIn("payload", json.dumps(verified))



if __name__ == "__main__":
    unittest.main()
