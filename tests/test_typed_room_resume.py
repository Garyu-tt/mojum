"""R0.15 existing typed-room restore: strict independent head, no auto-birth.

All events and keys here are synthetic and stored in a disposable temp folder.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError
from mojum.typed_room_resume import (
    TypedRoomResumeError, load_verified_typed_room,
)
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
ROOM = Room(4.5, 4, ((2.0, 1, 0.125, 2.0),))
START = (1.25, 2.5)
OWNER = "dante-fixture-001"


def run_cli(db, *, head=None, ticks=None, typed=True, restore=True, policy="local_cell_v2"):
    args = [
        sys.executable, "-m", "mojum.aquarium",
        "--db", str(db), "--exploration-mode", "goal_free",
        "--developmental-policy", policy,
    ]
    if typed:
        args += ["--room-numeric-contract", "typed_v1"]
    if restore:
        args += ["--resume-typed-room"]
    if head is not None:
        args += ["--expected-journal-head", head]
    if ticks is not None:
        args += ["--ticks", str(ticks)]
    return subprocess.run(
        args, cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True, text=True, timeout=35, check=False,
    )


class AnchoredTypedRoomResumeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "synthetic-private.sqlite3"

    def create_original(self, *, typed=True, ticks=11):
        with Aquarium(
                self.db, SEED, GRAPH, room=ROOM, start=START,
                exploration_mode="goal_free",
                developmental_policy="local_cell_v2b",
                room_numeric_contract="typed_v1" if typed else "legacy") as q:
            for _ in range(ticks):
                q.step()
            return q.store.head(), q.store.count(), q.checkpoint()

    def test_readonly_preflight_returns_original_mixed_types_no_write(self):
        head, count, checkpoint = self.create_original()
        room, start, manifest = load_verified_typed_room(
            self.db, agent_id=OWNER, expected_journal_head=head)
        self.assertEqual(manifest["schema"], "mojum.aquarium.bootstrap/2")
        self.assertEqual(type(room.width), float)
        self.assertEqual(type(room.height), int)
        self.assertEqual([type(x) for x in room.obstacles[0]],
                         [float, int, float, float])
        self.assertEqual(start, START)
        with Aquarium(
                self.db, SEED, GRAPH, room=room, start=start,
                external_expected_head=head,
                exploration_mode="goal_free", developmental_policy="local_cell_v2",
                room_numeric_contract="typed_v1") as reopened:
            self.assertEqual(reopened.checkpoint(), checkpoint)
            self.assertEqual(reopened.store.count(), count)
        with Aquarium(
                self.db, SEED, GRAPH, room=room, start=start,
                external_expected_head=head,
                exploration_mode="goal_free", developmental_policy="local_cell_v2",
                room_numeric_contract="typed_v1",
                read_only_existing=True) as inspect_only:
            self.assertEqual(inspect_only.checkpoint(), checkpoint)
            self.assertEqual(
                inspect_only.store._db.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaisesRegex(AquariumError, "strictly read-only"):
                inspect_only.step()
            with self.assertRaisesRegex(AquariumError, "strictly read-only"):
                inspect_only.acquire({"event_id": "malicious"})
            with self.assertRaisesRegex(AquariumError, "cannot create a replay"):
                inspect_only.save_replay_snapshot()
            with self.assertRaisesRegex(NoosphereError, "read-only"):
                inspect_only.store.append({}, expected_head=head)
        self.assertEqual(
            load_verified_typed_room(
                self.db, agent_id=OWNER, expected_journal_head=head)[1], start)

    def test_cli_resumes_nondefault_geometry_without_appending_by_default(self):
        head, count, original = self.create_original()
        result = run_cli(self.db, head=head)
        self.assertEqual(result.returncode, 0, result.stderr[-950:])
        reported = json.loads(result.stdout)
        self.assertEqual(reported["tick"], original["tick"])
        self.assertEqual(reported["journal_head"], head)
        self.assertEqual(reported["stored_events"], count)
        self.assertEqual(reported["world_revision"], original["world_revision"])
        self.assertEqual(reported["manifest_sha256"], original["manifest_sha256"])
        with closing(sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], count)

        # Continuing to write requires an explicitly supplied positive tick.
        resumed = run_cli(self.db, head=head, ticks=2)
        self.assertEqual(resumed.returncode, 0, resumed.stderr[-950:])
        after = json.loads(resumed.stdout)
        self.assertEqual(after["stored_events"], count + 2)
        self.assertEqual(after["tick"], original["tick"] + 2)
        self.assertNotEqual(after["journal_head"], head)
        with self.assertRaisesRegex(TypedRoomResumeError, "hash-chain"):
            load_verified_typed_room(
                self.db, agent_id=OWNER, expected_journal_head=head)

    def test_wrong_or_missing_external_anchor_never_appends(self):
        head, count, _ = self.create_original()
        cases = [
            run_cli(self.db, head=None),
            run_cli(self.db, head="f" * 64),
            run_cli(self.db, head="not-a-hash"),
            run_cli(self.db, head=head, typed=False),
            run_cli(self.db, head=head, restore=False),
            run_cli(self.db, head=head, policy="local_cell_v3"),
        ]
        for result in cases:
            self.assertNotEqual(result.returncode, 0)
        with closing(sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], count)
            self.assertEqual(
                db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0],
                head,
            )

    def test_read_only_existing_requires_head_and_does_not_birth(self):
        missing = self.root / "new.sqlite3"
        with self.assertRaisesRegex(AquariumError, "must already exist"):
            Aquarium(
                missing, SEED, GRAPH, exploration_mode="goal_free",
                developmental_policy="local_cell_v2",
                room_numeric_contract="typed_v1",
                external_expected_head="f" * 64,
                read_only_existing=True)
        self.assertFalse(missing.exists())
        head, _, _ = self.create_original()
        with self.assertRaisesRegex(AquariumError, "independently supplied"):
            Aquarium(
                self.db, SEED, GRAPH, room=ROOM, start=START,
                exploration_mode="goal_free",
                developmental_policy="local_cell_v2",
                room_numeric_contract="typed_v1",
                read_only_existing=True)
        with self.assertRaisesRegex(AquariumError, "exact typed_v1"):
            Aquarium(
                self.db, SEED, GRAPH, room=ROOM, start=START,
                exploration_mode="goal_free",
                developmental_policy="local_cell_v2",
                room_numeric_contract="legacy",
                external_expected_head=head,
                read_only_existing=True)

    def test_nonexistent_database_and_legacy_bootstrap_reject(self):
        nonexistent = self.root / "absent.sqlite3"
        result = run_cli(nonexistent, head="f" * 64, ticks=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(nonexistent.exists())
        head, count, _ = self.create_original(typed=False)
        with self.assertRaisesRegex(TypedRoomResumeError, "bootstrap/2"):
            load_verified_typed_room(
                self.db, agent_id=OWNER, expected_journal_head=head)
        self.assertNotEqual(run_cli(self.db, head=head).returncode, 0)
        with closing(sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], count)

    def test_forged_duplicate_key_copy_rejected_before_any_room_trust(self):
        head, _, _ = self.create_original()
        dirty = self.root / "duplicate-key.private.sqlite3"
        with closing(sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as source:
            with closing(sqlite3.connect(dirty)) as target:
                source.backup(target)
        with closing(sqlite3.connect(dirty)) as conn:
            conn.execute("DROP TRIGGER noosphere_no_update")
            raw = conn.execute("SELECT wire_json FROM events WHERE seq=1").fetchone()[0]
            conn.execute("UPDATE events SET wire_json=? WHERE seq=1",
                         ('{"event_id":"forged-duplicate",' + raw[1:],))
            conn.commit()
        with self.assertRaisesRegex(TypedRoomResumeError, "hash-chain"):
            load_verified_typed_room(
                dirty, agent_id=OWNER, expected_journal_head=head)
        self.assertNotEqual(run_cli(dirty, head=head, ticks=1).returncode, 0)

    def test_owner_mismatch_refused_before_bootstrap_adoption(self):
        head, _, _ = self.create_original()
        with self.assertRaisesRegex(TypedRoomResumeError, "verification failed"):
            load_verified_typed_room(
                self.db, agent_id="someone-else", expected_journal_head=head)


if __name__ == "__main__":
    unittest.main()
