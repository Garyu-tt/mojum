"""Mentoring resumes verified twins without creating or repairing history."""

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

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import LocalNoosphere, NoosphereError
import mojum.twin_lab as twin_lab


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
LEFT_ID = "mentor-admission-left"
RIGHT_ID = "mentor-admission-right"
SIDECARS = ("-wal", "-shm", "-journal")


def twin_seed(agent_id=LEFT_ID):
    seed = copy.deepcopy(SEED)
    seed["genome_id"] = agent_id
    return seed


class TwinMentorAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.left = self.directory / "left.sqlite3"
        self.right = self.directory / "right.sqlite3"

    def pair(self):
        return twin_lab.create_pair(
            self.left, self.right, LEFT_ID, RIGHT_ID, SEED, GRAPH,
        )

    def mentor(self, path=None, **kwargs):
        command = {"ticks": 1, "dx": 0.02, "dy": 0.0}
        command.update(kwargs)
        return twin_lab.mentor_steps(
            self.left if path is None else path, LEFT_ID, SEED, GRAPH,
            **command,
        )

    def rollback_mode(self, path):
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        if os.name == "posix":
            path.chmod(0o640)

    def file_state(self, path):
        return {
            "bytes": path.read_bytes(),
            "mode": stat.S_IMODE(path.stat().st_mode),
            "sidecars": {
                suffix: Path(str(path) + suffix).exists()
                for suffix in SIDECARS
            },
        }

    def assert_unchanged(self, path, before):
        after = self.file_state(path)
        self.assertTrue(after["bytes"] == before["bytes"], "database bytes changed")
        self.assertEqual(after["mode"], before["mode"])
        self.assertEqual(after["sidecars"], before["sidecars"])

    def nonjournal(self, kind, path):
        if kind == "empty":
            path.touch()
        elif kind == "unrelated":
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE personal (note TEXT NOT NULL)")
                db.execute("INSERT INTO personal VALUES ('retain this unrelated data')")
                db.commit()
        elif kind == "bootstrapless":
            with LocalNoosphere(path, LEFT_ID):
                pass
            self.rollback_mode(path)
        elif kind == "wrong_owner":
            with Aquarium(path, twin_seed(RIGHT_ID), GRAPH, agent_id=RIGHT_ID):
                pass
            self.rollback_mode(path)
        elif kind == "malformed":
            path.write_bytes(b"This is not a SQLite database.\n")
        else:
            raise AssertionError(kind)
        if os.name == "posix":
            path.chmod(0o640)

    def test_existing_nonjournals_are_rejected_without_mutation(self):
        for kind in ("empty", "unrelated", "bootstrapless", "wrong_owner", "malformed"):
            for ticks in (0, 1):
                with self.subTest(kind=kind, ticks=ticks):
                    path = self.directory / f"{kind}-{ticks}.sqlite3"
                    self.nonjournal(kind, path)
                    before = self.file_state(path)
                    with self.assertRaises((AquariumError, NoosphereError)):
                        self.mentor(path, ticks=ticks)
                    self.assert_unchanged(path, before)

    def test_corrupt_later_event_is_rejected_before_writable_initialization(self):
        self.pair()
        self.mentor(ticks=2)
        self.rollback_mode(self.left)
        with closing(sqlite3.connect(self.left)) as db:
            db.execute("DROP TRIGGER noosphere_no_update")
            db.execute("UPDATE events SET event_hash=? WHERE seq=3", ("f" * 64,))
            db.commit()
        before = self.file_state(self.left)
        with self.assertRaises((AquariumError, NoosphereError)):
            self.mentor()
        self.assert_unchanged(self.left, before)

    def test_hash_valid_unreplayable_history_is_rejected_without_mutation(self):
        self.pair()
        # The append-only store admits this valid generic event, but an
        # Aquarium cannot replay its unsupported topic. A tip check alone
        # is therefore insufficient to admit a writable mentor session.
        with LocalNoosphere(self.left, LEFT_ID) as store:
            event = store.dataset(include_nonpublic=True, limit=1)["projection"]["items"][0]["event"]
            event["event_id"] = "foreign-but-chain-valid-event"
            event["topic"] = "not.an.aquarium.transition"
            event["payload"] = {"note": "not a valid organism step"}
            store.append(event, expected_head=store.head())
        self.rollback_mode(self.left)
        before = self.file_state(self.left)
        with self.assertRaises((AquariumError, NoosphereError)):
            self.mentor()
        self.assert_unchanged(self.left, before)

    def test_exhausted_budget_is_rejected_without_mutation(self):
        self.pair()
        self.rollback_mode(self.left)
        before = self.file_state(self.left)
        # A one-event cap is sufficient to exercise the real preflight
        # boundary without writing 100,000 irrelevant fixture events.
        with patch.object(twin_lab, "MAX_REPLAY_EVENTS", 1):
            with self.assertRaises(AquariumError):
                self.mentor()
        self.assert_unchanged(self.left, before)

    def test_zero_steps_are_a_read_only_verified_inspection(self):
        self.pair()
        self.mentor(ticks=2)
        self.rollback_mode(self.left)
        before = self.file_state(self.left)
        report = self.mentor(ticks=0)
        self.assertEqual(report["twin"]["tick"], 2)
        self.assertEqual(report["twin"]["stored_events"], 3)
        self.assert_unchanged(self.left, before)

    def test_valid_mentor_continues_and_leaves_sibling_unchanged(self):
        self.pair()
        sibling_before = self.file_state(self.right)
        first = self.mentor(ticks=2)
        second = self.mentor(ticks=3, dx=-0.01)
        self.assertEqual((first["twin"]["tick"], first["twin"]["stored_events"]), (2, 3))
        self.assertEqual((second["twin"]["tick"], second["twin"]["stored_events"]), (5, 6))
        self.assertEqual(first["shared_heritable_sha256"], second["shared_heritable_sha256"])
        self.assert_unchanged(self.right, sibling_before)
        with Aquarium(self.left, twin_seed(), GRAPH, agent_id=LEFT_ID,
                      read_only=True) as reopened:
            self.assertEqual(reopened.tick, 5)
            self.assertEqual(reopened.stored_events, 6)
            self.assertEqual([reopened.world.x, reopened.world.y], second["twin"]["position_m"])

    def race_before_writable_open(self, mutate):
        seen = []

        def open_aquarium(*args, **kwargs):
            if not kwargs.get("read_only", False) and not seen:
                mutate()
                seen.append(True)
            return Aquarium(*args, **kwargs)

        with patch.object(twin_lab, "Aquarium", side_effect=open_aquarium):
            with self.assertRaises((AquariumError, NoosphereError)):
                self.mentor()
        self.assertEqual(seen, [True], "the race must reach the writable-open boundary")

    def test_removed_source_cannot_be_recreated_after_admission(self):
        self.pair()
        self.rollback_mode(self.left)
        self.race_before_writable_open(self.left.unlink)
        self.assertFalse(self.left.exists())
        for suffix in SIDECARS:
            self.assertFalse(Path(str(self.left) + suffix).exists())

    def test_empty_replacement_cannot_be_bootstrapped_after_admission(self):
        self.pair()
        self.rollback_mode(self.left)
        replacement = self.directory / "empty-replacement.sqlite3"
        self.nonjournal("empty", replacement)
        before = self.file_state(replacement)
        self.race_before_writable_open(lambda: os.replace(replacement, self.left))
        self.assert_unchanged(self.left, before)

    def test_different_valid_history_cannot_replace_admitted_history(self):
        self.pair()
        self.rollback_mode(self.left)
        replacement = self.directory / "other-history.sqlite3"
        with Aquarium(replacement, twin_seed(), GRAPH, agent_id=LEFT_ID) as other:
            other.step(dx=-0.03, dy=0)
        self.rollback_mode(replacement)
        before = self.file_state(replacement)
        self.race_before_writable_open(lambda: os.replace(replacement, self.left))
        self.assert_unchanged(self.left, before)

    def test_competing_writer_advancement_is_preserved_and_not_mentored(self):
        self.pair()
        self.rollback_mode(self.left)
        saved = {}

        def other_writer():
            with Aquarium(self.left, twin_seed(), GRAPH, agent_id=LEFT_ID) as other:
                other.step(dx=-0.03, dy=0)
                saved["checkpoint"] = other.checkpoint()
            self.rollback_mode(self.left)
            saved["file"] = self.file_state(self.left)

        self.race_before_writable_open(other_writer)
        self.assert_unchanged(self.left, saved["file"])
        with Aquarium(self.left, twin_seed(), GRAPH, agent_id=LEFT_ID,
                      read_only=True) as reopened:
            self.assertEqual(reopened.checkpoint(), saved["checkpoint"])
            self.assertEqual(reopened.tick, 1)

    def test_cli_rejects_nonjournals_without_success_receipt_or_mutation(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(twin_lab.__file__).resolve().parents[1])
        for kind in ("empty", "unrelated", "bootstrapless", "wrong_owner", "malformed"):
            with self.subTest(kind=kind):
                path = self.directory / f"cli-{kind}.sqlite3"
                self.nonjournal(kind, path)
                before = self.file_state(path)
                completed = subprocess.run(
                    [sys.executable, "-m", "mojum.twin_lab", "mentor",
                     "--db", str(path), "--agent-id", LEFT_ID, "--ticks", "1"],
                    cwd=self.directory, env=environment, capture_output=True,
                    text=True, timeout=15,
                )
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("Traceback", completed.stderr)
                self.assert_unchanged(path, before)


if __name__ == "__main__":
    unittest.main()
