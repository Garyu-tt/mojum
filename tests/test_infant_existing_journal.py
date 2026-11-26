"""An existing file must be infant history before writable initialization."""

from contextlib import closing
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

import mojum.infant_play as infant_module
from mojum.infant_play import InfantPlay
from mojum.noosphere import LocalNoosphere
from mojum.sensory_curiosity_lab import SensoryPlay
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
ACTOR = "dante-fixture-001"


class InfantExistingJournalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def _foreign_database(self, path):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE user_notes (body TEXT)")
            db.execute("INSERT INTO user_notes VALUES (?)", ("keep this database unchanged",))

    def _delete_mode(self, path):
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")

    def _reject_without_write(self, path, **options):
        if os.name == "posix":
            path.chmod(0o640)
        before = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        with patch.object(infant_module, "LocalNoosphere", wraps=LocalNoosphere) as opener:
            with self.assertRaises(ValueError):
                with InfantPlay(path, GRAPH, **options):
                    pass
        opener.assert_not_called()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(path) + suffix).exists())

    def test_existing_empty_and_unrelated_files_are_not_adopted(self):
        for kind in ("empty_file", "foreign_database", "empty_noosphere"):
            with self.subTest(kind=kind):
                path = self.directory / f"{kind}.sqlite3"
                if kind == "empty_file":
                    path.touch()
                elif kind == "foreign_database":
                    self._foreign_database(path)
                else:
                    with LocalNoosphere(path, ACTOR):
                        pass
                    self._delete_mode(path)
                self._reject_without_write(path)

    def test_missing_or_empty_metadata_is_not_repaired(self):
        for sql in ("DROP TABLE metadata", "DELETE FROM metadata"):
            with self.subTest(sql=sql):
                path = self.directory / ("missing.sqlite3" if "DROP" in sql else "empty.sqlite3")
                with InfantPlay(path, GRAPH) as infant:
                    infant.step()
                self._delete_mode(path)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute(sql)
                self._reject_without_write(path)

    def test_incompatible_history_is_rejected_before_writable_open(self):
        for kind in ("owner", "seed", "clock", "room", "different_lab"):
            with self.subTest(kind=kind):
                path = self.directory / f"{kind}.sqlite3"
                if kind == "different_lab":
                    with SensoryPlay(path, GRAPH, agent_id=ACTOR) as infant:
                        infant.step()
                else:
                    with InfantPlay(path, GRAPH) as infant:
                        infant.step()
                self._delete_mode(path)
                options = {
                    "owner": {"agent_id": "another-owner"},
                    "seed": {"seed": 1234},
                    "clock": {"dt_ns": 20_000_000},
                    "room": {"room": Room(6, 6)},
                    "different_lab": {},
                }[kind]
                self._reject_without_write(path, **options)

    def test_bootstrap_json_number_alias_is_rejected_before_writable_open(self):
        path = self.directory / "number-alias.sqlite3"
        with InfantPlay(path, GRAPH):
            pass
        self._delete_mode(path)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("DROP TRIGGER noosphere_no_update")
            event = json.loads(db.execute("SELECT wire_json FROM events WHERE seq=1").fetchone()[0])
            event["payload"]["dt_ns"] = float(event["payload"]["dt_ns"])
            db.execute("UPDATE events SET wire_json=? WHERE seq=1", (json.dumps(event),))
        self._reject_without_write(path)

    def test_new_journal_and_cold_continuation_preserve_determinism(self):
        for room in (Room(4, 4), Room(4.0, 4.0)):
            with self.subTest(room=room):
                name = "integer" if type(room.width) is int else "float"
                path = self.directory / f"{name}.sqlite3"
                control = self.directory / f"{name}-control.sqlite3"
                with InfantPlay(path, GRAPH, room=room) as infant:
                    for _ in range(3):
                        infant.step()
                    before = infant.checkpoint()
                with InfantPlay(path, GRAPH, room=room) as infant:
                    self.assertEqual(infant.checkpoint(), before)
                    for _ in range(2):
                        infant.step()
                    resumed = infant.checkpoint()
                with InfantPlay(control, GRAPH, room=room) as infant:
                    for _ in range(5):
                        infant.step()
                    self.assertEqual(infant.checkpoint(), resumed)

    def _cli(self, path, ticks):
        return subprocess.run(
            [sys.executable, "-m", "mojum.infant_play", "--db", str(path),
             "--ticks", str(ticks)],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            cwd=self.directory, capture_output=True, text=True, timeout=30,
        )

    def test_cli_zero_ticks_preserves_unrelated_database(self):
        path = self.directory / "application.sqlite3"
        self._foreign_database(path)
        before = path.read_bytes()
        result = self._cli(path, 0)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(Path(str(path) + "-wal").exists())
        self.assertFalse(Path(str(path) + "-shm").exists())
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT body FROM user_notes").fetchall(),
                             [("keep this database unchanged",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall(),
                             [("user_notes",)])

    def test_cli_new_path_and_restart_match_continuous_run(self):
        path = self.directory / "cli.sqlite3"
        first = self._cli(path, 2)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["events"], 3)
        resumed = self._cli(path, 1)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        control = self._cli(self.directory / "continuous.sqlite3", 3)
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertEqual(json.loads(resumed.stdout), json.loads(control.stdout))


if __name__ == "__main__":
    unittest.main()
