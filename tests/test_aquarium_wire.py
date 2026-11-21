"""Read-only layout identification and rejection of wrong writable opens."""

from contextlib import closing
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_wire import inspect_journal
from mojum.noosphere import LocalNoosphere, NoosphereError


DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))


class AquariumWireTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.db = Path(scratch.name) / "synthetic.sqlite3"

    def aquarium(self, motor_mode="scripted"):
        return Aquarium(self.db, SEED, GRAPH, motor_mode=motor_mode)

    def make_rollback_journal(self):
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        if os.name == "posix":
            self.db.chmod(0o640)
        return self.db.read_bytes()

    def test_wrong_mode_does_not_change_sqlite_file_or_permissions(self):
        with self.aquarium() as aquarium:
            aquarium.step()
            saved = aquarium.checkpoint()
        wire = self.make_rollback_journal()
        mode = self.db.stat().st_mode
        with self.assertRaisesRegex(AquariumError, "mode mismatch"):
            self.aquarium("developmental_fixture")
        self.assertEqual(self.db.read_bytes(), wire)
        self.assertEqual(self.db.stat().st_mode, mode)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with self.aquarium() as restored:
            self.assertEqual(restored.checkpoint(), saved)
            self.assertEqual(restored.step()["tick"], 1)

    def test_inspector_reports_mode_and_anchor_with_writer_wal_open(self):
        with self.aquarium("developmental_fixture") as aquarium:
            aquarium.step()
            aquarium.advance_development()
            aquarium.step()
            checkpoint = aquarium.checkpoint()
            wal = self.db.with_suffix(".sqlite3-wal")
            journal_before = self.db.read_bytes()
            wal_before = wal.read_bytes()
            actual = inspect_journal(self.db, external_expected_head=checkpoint["journal_head"])
            self.assertEqual(actual, {
                "schema": "mojum.aquarium.wire-inspection/1",
                "format": "motor_play_v1",
                "state_schema": "mojum.developmental.motor-play/1",
                "temporal_schema": None,
                "frames": 2,
                "events": 4,
                "journal_head": checkpoint["journal_head"],
                "integrity": "chain-and-independent-head-checked",
            })
            with self.assertRaisesRegex(NoosphereError, "independent external anchor"):
                inspect_journal(self.db, external_expected_head="1" * 64)
            self.assertEqual(self.db.read_bytes(), journal_before)
            self.assertEqual(wal.read_bytes(), wal_before)
            self.assertEqual(aquarium.checkpoint(), checkpoint)

    def test_inspector_refuses_mixed_policy_bootstrap_even_with_valid_hash(self):
        with self.aquarium() as aquarium:
            event = aquarium.store.dataset(include_nonpublic=True)["projection"]["items"][0]["event"]
        foreign = self.db.with_name("mixed.sqlite3")
        event["payload"].update({
            "exploration_mode": "goal_free",
            "motor_mode": "developmental_fixture",
            "motor_policy_schema": "mojum.developmental.motor-play/1",
        })
        owner = event["agent_id"]
        with LocalNoosphere(foreign, owner) as store:
            head = store.append(event, expected_head=store.head())
            self.assertEqual(store.verify_chain()["head"], head)
        before = foreign.read_bytes()
        with self.assertRaisesRegex(NoosphereError, "ambiguous or unknown"):
            inspect_journal(foreign, external_expected_head=head)
        self.assertEqual(foreign.read_bytes(), before)

    def test_missing_file_and_empty_store(self):
        with self.assertRaises(NoosphereError):
            inspect_journal(self.db)
        self.assertFalse(self.db.exists())
        self.db.touch()
        with self.aquarium() as stocked:
            self.assertEqual(stocked.tick, 0)
        self.assertEqual(inspect_journal(self.db)["format"], "scripted_v1")


if __name__ == "__main__":
    unittest.main()
