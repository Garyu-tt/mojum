"""Cold-copy schema admission follows SQLite identifier case semantics."""

from contextlib import closing
from importlib.resources import files
import itertools
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_cold_copy import _require_existing_source_journal
from mojum.noosphere import LocalNoosphere


DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))
OWNER = SEED["genome_id"]
ROOT = Path(__file__).resolve().parents[1]


class ColdCopyTableCaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.serial = 0

    def journal(self, metadata="Metadata", events="Events", *, empty=False):
        self.serial += 1
        path = self.root / f"journal-{self.serial}.sqlite3"
        if empty:
            with LocalNoosphere(path, OWNER):
                pass
        else:
            with Aquarium(path, SEED, GRAPH) as aquarium:
                aquarium.step()
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone(),
                             ("delete",))
            for original, renamed in (("metadata", metadata), ("events", events)):
                if original != renamed:
                    db.execute(f'ALTER TABLE "{original}" RENAME TO rename_temporary')
                    db.execute(f'ALTER TABLE rename_temporary RENAME TO "{renamed}"')
        return path

    def inventory(self):
        return {path.name: (path.stat().st_mode, path.read_bytes())
                for path in self.root.iterdir()}

    def assert_rejected_unchanged(self, path, message):
        before = self.inventory()
        with self.assertRaisesRegex(ValueError, message):
            _require_existing_source_journal(path, OWNER)
        self.assertEqual(self.inventory(), before)

    def run_module(self, name, *arguments):
        isolation = ["-I"] if sys.flags.isolated else []
        result = subprocess.run(
            [sys.executable, *isolation, "-m", "mojum." + name,
             *map(str, arguments)], cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def test_all_table_capitalizations_preserve_real_history(self):
        for metadata, events in itertools.product(
                ("metadata", "Metadata", "METADATA", "mEtAdAtA"),
                ("events", "Events", "EVENTS", "eVeNtS")):
            with self.subTest(metadata=metadata, events=events):
                path = self.journal(metadata, events)
                before = self.inventory()
                _require_existing_source_journal(path, OWNER)
                self.assertEqual(self.inventory(), before)

    def test_cli_export_verify_and_independent_resume_for_table_capitalizations(self):
        for metadata, events in itertools.product(
                ("metadata", "Metadata", "METADATA", "mEtAdAtA"),
                ("events", "Events", "EVENTS", "eVeNtS")):
            with self.subTest(metadata=metadata, events=events):
                source = self.journal(metadata, events)
                backup = source.with_suffix(".copy.sqlite3")
                with Aquarium(source, SEED, GRAPH, read_only=True) as aquarium:
                    expected = aquarium.checkpoint()
                original = (source.stat().st_mode, source.read_bytes())
                exported = self.run_module("aquarium_cold_copy", "export",
                                           "--db", source, "--output", backup)
                self.assertEqual(exported["journal_head"], expected["journal_head"])
                self.assertEqual((exported["tick"], exported["event_count"]), (1, 2))
                self.assertEqual((source.stat().st_mode, source.read_bytes()), original)
                before_verify = self.inventory()
                verified = self.run_module(
                    "aquarium_cold_copy", "verify", "--db", backup,
                    "--expected-head", exported["journal_head"],
                    "--expected-sha256", exported["db_sha256"], "--expected-tick", 1,
                )
                self.assertEqual(verified["journal_head"], expected["journal_head"])
                self.assertEqual(verified["event_count"], 2)
                self.assertEqual(self.inventory(), before_verify)
                with closing(sqlite3.connect(backup)) as db:
                    names = {row[0] for row in db.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table'"
                    )}
                    self.assertIn(metadata, names)
                    self.assertIn(events, names)
                continued = self.run_module("aquarium", "--db", source, "--ticks", 1)
                restored = self.run_module("aquarium", "--db", backup, "--ticks", 1)
                self.assertEqual(continued, restored)
                self.assertEqual((restored["tick"], restored["stored_events"]), (2, 3))

    def test_views_cannot_substitute_for_case_insensitive_tables(self):
        for table in ("Metadata", "Events"):
            with self.subTest(table=table):
                path = self.journal()
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(f'ALTER TABLE "{table}" RENAME TO saved_table')
                    db.execute(f'CREATE VIEW "{table}" AS SELECT * FROM saved_table')
                self.assert_rejected_unchanged(path, "existing metadata and events tables")

    def test_mixed_case_tables_require_exact_metadata(self):
        for mutation in ("owner", "schema", "empty", "extra"):
            with self.subTest(mutation=mutation):
                path = self.journal("mEtAdAtA", "eVeNtS")
                with closing(sqlite3.connect(path)) as db, db:
                    if mutation in ("owner", "schema"):
                        db.execute("UPDATE metadata SET v=? WHERE k=?", ("wrong", mutation))
                    elif mutation == "empty":
                        db.execute("DELETE FROM metadata")
                    else:
                        db.execute("INSERT INTO metadata VALUES ('extra', 'unrecognized')")
                self.assert_rejected_unchanged(path, "complete matching journal metadata")

    def test_mixed_case_empty_history_cannot_be_initialized(self):
        path = self.journal("METADATA", "EVENTS", empty=True)
        self.assert_rejected_unchanged(path, "existing committed Aquarium event")


if __name__ == "__main__":
    unittest.main()
