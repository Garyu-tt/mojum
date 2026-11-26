"""A blank reservation is not the same as an unrelated SQLite schema."""

from contextlib import closing
import copy
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

import mojum.aquarium as aquarium_module
from mojum.aquarium import Aquarium
from mojum.noosphere import LocalNoosphere, NoosphereError, _read_first_event
from mojum.twin_lab import create_pair


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
ACTOR = SEED["genome_id"]


class AquariumSchemaAdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def _foreign(self, path, *, view=False):
        with closing(sqlite3.connect(path)) as db, db:
            if view:
                # Admission must inspect schema existence, not evaluate views.
                db.execute("CREATE VIEW app_status AS SELECT app_only_function() AS body")
            else:
                db.execute("CREATE TABLE user_notes (body TEXT)")
                db.execute("INSERT INTO user_notes VALUES ('preserve application data')")

    def _reject_without_writable_open(self, path):
        if os.name == "posix":
            path.chmod(0o640)
        before = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        with patch.object(aquarium_module, "LocalNoosphere", wraps=LocalNoosphere) as writer:
            with self.assertRaisesRegex(NoosphereError, "existing database has no event journal"):
                with Aquarium(path, SEED, GRAPH):
                    pass
        writer.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(os.path.lexists(str(path) + suffix))

    def test_foreign_table_and_view_are_rejected_before_writable_open(self):
        for view in (False, True):
            with self.subTest(view=view):
                path = self.root / f"foreign-{view}.sqlite3"
                self._foreign(path, view=view)
                self._reject_without_writable_open(path)

    def test_missing_events_table_is_not_recreated(self):
        path = self.root / "incomplete.sqlite3"
        with Aquarium(path, SEED, GRAPH) as aquarium:
            aquarium.step()
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
            db.execute("DROP TABLE events")
        self._reject_without_writable_open(path)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema WHERE name='events'").fetchall(), [])
            self.assertEqual(dict(db.execute("SELECT k,v FROM metadata"))["owner"], ACTOR)

    def test_read_only_inspection_keeps_absent_bootstrap_semantics(self):
        path = self.root / "inspect.sqlite3"
        self._foreign(path)
        before = path.read_bytes()
        self.assertIsNone(_read_first_event(path, ACTOR))
        self.assertEqual(path.read_bytes(), before)

    def _rename_tables(self, path, *, metadata, events):
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
            for name, changed in (("metadata", metadata), ("events", events)):
                if changed:
                    db.execute(f"ALTER TABLE {name} RENAME TO rename_temporary")
                    db.execute(f"ALTER TABLE rename_temporary RENAME TO {name.title()}")

    def test_mixed_case_tables_preserve_valid_history_and_anchored_restart(self):
        for metadata, events in ((True, False), (False, True), (True, True)):
            with self.subTest(metadata=metadata, events=events):
                path = self.root / f"history-{metadata}-{events}.sqlite3"
                with Aquarium(path, SEED, GRAPH) as aquarium:
                    aquarium.step()
                    expected = aquarium.checkpoint()
                self._rename_tables(path, metadata=metadata, events=events)
                with Aquarium(path, SEED, GRAPH,
                              external_expected_head=expected["journal_head"]) as reopened:
                    self.assertEqual(reopened.checkpoint(), expected)
                    reopened.step()
                    self.assertEqual(reopened.stored_events, 3)

    def test_mixed_case_empty_noosphere_still_bootstraps(self):
        path = self.root / "mixed-empty.sqlite3"
        with LocalNoosphere(path, ACTOR):
            pass
        self._rename_tables(path, metadata=True, events=True)
        with Aquarium(path, SEED, GRAPH) as aquarium:
            self.assertEqual(aquarium.stored_events, 1)
            aquarium.step()
            expected = aquarium.checkpoint()
        with Aquarium(path, SEED, GRAPH) as reopened:
            self.assertEqual(reopened.checkpoint(), expected)

    def test_mixed_case_wrong_owner_is_rejected_before_writable_open(self):
        path = self.root / "mixed-owner.sqlite3"
        with Aquarium(path, SEED, GRAPH) as aquarium:
            aquarium.step()
        self._rename_tables(path, metadata=True, events=True)
        if os.name == "posix":
            path.chmod(0o640)
        before, mode = path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
        seed = copy.deepcopy(SEED)
        seed["genome_id"] = "wrong-owner"
        with patch.object(aquarium_module, "LocalNoosphere", wraps=LocalNoosphere) as writer:
            with self.assertRaisesRegex(NoosphereError, "schema or agent owner does not match"):
                with Aquarium(path, seed, GRAPH, agent_id=seed["genome_id"]):
                    pass
        writer.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        self.assertFalse(os.path.lexists(str(path) + "-wal"))
        self.assertFalse(os.path.lexists(str(path) + "-shm"))

    def test_blank_and_initialized_empty_journals_still_bootstrap(self):
        for kind in ("missing", "reservation", "empty_sqlite", "empty_noosphere"):
            with self.subTest(kind=kind):
                path = self.root / f"{kind}.sqlite3"
                if kind == "reservation":
                    path.touch(mode=0o600)
                elif kind == "empty_sqlite":
                    with closing(sqlite3.connect(path)) as db:
                        db.execute("VACUUM")
                        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0], 0)
                    self.assertGreater(path.stat().st_size, 0)
                elif kind == "empty_noosphere":
                    with LocalNoosphere(path, ACTOR) as store:
                        self.assertEqual(store.count(), 0)
                with Aquarium(path, SEED, GRAPH) as aquarium:
                    self.assertEqual(aquarium.stored_events, 1)
                    self.assertEqual(aquarium.tick, 0)
                    aquarium.step()
                    expected = aquarium.checkpoint()
                with Aquarium(path, SEED, GRAPH) as reopened:
                    self.assertEqual(reopened.checkpoint(), expected)

    def test_valid_history_continues_at_the_verified_head(self):
        path = self.root / "history.sqlite3"
        with Aquarium(path, SEED, GRAPH) as aquarium:
            aquarium.step(dx=0.02)
            before = aquarium.checkpoint()
        with Aquarium(path, SEED, GRAPH,
                      external_expected_head=before["journal_head"]) as reopened:
            self.assertEqual(reopened.checkpoint(), before)
            reopened.step(dx=0.02)
            continued = reopened.checkpoint()
        with Aquarium(self.root / "control.sqlite3", SEED, GRAPH) as control:
            control.step(dx=0.02)
            control.step(dx=0.02)
            self.assertEqual(control.checkpoint(), continued)

    def test_twin_pair_can_initialize_its_exclusive_blank_reservations(self):
        left, right = self.root / "left.sqlite3", self.root / "right.sqlite3"
        receipt = create_pair(left, right, "schema-twin-left", "schema-twin-right", SEED, GRAPH)
        self.assertEqual([twin["stored_events"] for twin in receipt["twins"]], [1, 1])
        for path, actor in ((left, "schema-twin-left"), (right, "schema-twin-right")):
            seed = copy.deepcopy(SEED)
            seed["genome_id"] = actor
            with Aquarium(path, seed, GRAPH, agent_id=actor) as twin:
                self.assertEqual(twin.tick, 0)
                twin.step()
                self.assertEqual(twin.stored_events, 2)

    def test_cli_zero_ticks_preserves_foreign_database_and_emits_no_checkpoint(self):
        path = self.root / "cli.sqlite3"
        self._foreign(path)
        before = path.read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium", "--db", str(path), "--ticks", "0"],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), cwd=self.root,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("existing database has no event journal", result.stderr)
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT body FROM user_notes").fetchall(),
                             [("preserve application data",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema").fetchall(),
                             [("user_notes",)])


if __name__ == "__main__":
    unittest.main()
