"""Executable CPU-first custom room CLI acceptance: new journal then cold resume.

No real user journals. Every test uses isolated disposable local SQLite.
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

from mojum.room_contract import RoomContractError
from mojum.room_input import read_new_room_contract
from mojum.world_room import _revision

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/room_exact_mixed.json"
SIMPLE = ROOT / "examples/room_simple_mixed.json"


def invoke(database, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "mojum.aquarium", "--db", str(database),
         *args],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True, text=True, timeout=35,
    )
    return proc


def _stored(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                 uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        events = db.execute(
            "SELECT seq,wire_json,event_hash FROM events ORDER BY seq"
        ).fetchall()
    return events


def new_args(file, *, ticks=12):
    return [
        "--room-numeric-contract", "typed_v1",
        "--new-room-contract", str(file),
        "--developmental-policy", "local_cell_v2",
        "--exploration-mode", "goal_free",
        "--ticks", str(ticks),
    ]


class NewTypedRoomCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mojum-room-file-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "new-private.sqlite3"

    def test_example_loader_preserves_mixed_numeric_scalar_revision(self):
        room, start = read_new_room_contract(EXAMPLE)
        self.assertEqual((room.width, room.height, start), (4.5, 4, (1.25, 2.5)))
        self.assertEqual(type(room.width), float)
        self.assertEqual(type(room.height), int)
        self.assertEqual([type(x) for x in room.obstacles[0]],
                         [float, int, float, float])
        self.assertEqual(type(start[0]), float)
        self.assertEqual(_revision(room, 0, *start),
                         _revision(room, 0, 1.25, 2.5))

    def test_beginner_decimal_json_matches_exact_persisted_abi(self):
        expected_room, expected_start = read_new_room_contract(EXAMPLE)
        draft_room, draft_start = read_new_room_contract(SIMPLE)
        self.assertEqual(expected_start, draft_start)
        self.assertEqual(type(expected_room.width), type(draft_room.width))
        self.assertEqual(type(expected_room.height), type(draft_room.height))
        self.assertEqual(
            [type(x) for x in expected_room.obstacles[0]],
            [type(x) for x in draft_room.obstacles[0]],
        )
        self.assertEqual(
            _revision(expected_room, 0, *expected_start),
            _revision(draft_room, 0, *draft_start),
        )
        # Both distinct authoring formats MUST produce one identical binary
        # persistence contract, even through the real Aquarium subprocess.
        original = invoke(self.db, *new_args(EXAMPLE, ticks=24))
        other_db = self.root / "beginner.sqlite3"
        beginner = invoke(other_db, *new_args(SIMPLE, ticks=24))
        self.assertEqual(original.returncode, 0, original.stderr[-900:])
        self.assertEqual(beginner.returncode, 0, beginner.stderr[-900:])
        self.assertEqual(_stored(self.db), _stored(other_db))
        self.assertEqual(json.loads(original.stdout), json.loads(beginner.stdout))
        self.assertEqual(
            json.loads(_stored(other_db)[0][1])["payload"]["room_exact"]["schema"],
            "mojum.world.room-exact-scalars/1",
        )
        self.assertNotIn(
            "point-room-draft",
            _stored(other_db)[0][1],
        )
        # Recovery from either journal does not require the original JSON.
        head = json.loads(beginner.stdout)["journal_head"]
        restore = invoke(
            other_db, "--room-numeric-contract", "typed_v1",
            "--resume-typed-room", "--expected-journal-head", head,
            "--developmental-policy", "local_cell_v2",
            "--exploration-mode", "goal_free")
        self.assertEqual(restore.returncode, 0, restore.stderr[-900:])
        self.assertEqual(json.loads(restore.stdout)["journal_head"], head)

    def test_beginner_numeric_type_changes_affect_world_revision(self):
        raw = json.loads(SIMPLE.read_text(encoding="utf-8"))
        first_room, start = read_new_room_contract(SIMPLE)
        changed = dict(raw, height=4.0)
        changed_path = self.root / "float-height.json"
        changed_path.write_text(json.dumps(changed), encoding="utf-8")
        second_room, new_start = read_new_room_contract(changed_path)
        self.assertEqual(first_room.height, second_room.height)
        self.assertIs(type(first_room.height), int)
        self.assertIs(type(second_room.height), float)
        self.assertNotEqual(_revision(first_room, 0, *start),
                            _revision(second_room, 0, *new_start))

    def test_reject_invalid_beginner_json_without_reserved_target(self):
        base = json.loads(SIMPLE.read_text(encoding="utf-8"))
        cases = {
            "extra_key": dict(base, unexplained="reject"),
            "nonpositive": dict(base, width=0),
            "huge_dimension": dict(base, width=1001),
            "bool_dimension": dict(base, width=True),
            "bool_start": dict(base, start=[True, 2.5]),
            "overflow_integer": dict(base, width=2**64),
            "string": dict(base, width="4.5"),
            "tuple_shape": dict(base, obstacles=[[2, 1, 0.125]]),
            "blocked_start": dict(base, start=[2.01, 2.5]),
            "outside_start": dict(base, start=[4.6, 2.5]),
            "overquota": dict(base, obstacles=[[2.0, 1, 0.125, 2.0]] * 4097),
            "nonfinite": dict(base, width=float("inf")),
            "wrong_version": dict(base, schema="mojum.world.point-room-draft/999"),
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                path = self.root / (label + "-draft.json")
                target = self.root / (label + "-private.sqlite3")
                path.write_text(json.dumps(value), encoding="utf-8")
                result = invoke(target, *new_args(path, ticks=1))
                self.assertNotEqual(result.returncode, 0, (label, result.stdout))
                self.assertFalse(target.exists())

    def test_actual_fresh_room_resume_zero_append_then_explicit_continue(self):
        first = invoke(self.db, *new_args(EXAMPLE, ticks=25))
        self.assertEqual(first.returncode, 0, first.stderr[-1100:])
        created = json.loads(first.stdout)
        saved = _stored(self.db)
        self.assertEqual(created["tick"], 25)
        self.assertEqual(len(saved), 26)
        self.assertEqual(saved[-1][2], created["journal_head"])
        manifest = json.loads(saved[0][1])["payload"]
        self.assertEqual(manifest["schema"], "mojum.aquarium.bootstrap/2")
        self.assertEqual(manifest["room_exact"]["width"],
                         {"type": "float", "hex": "0x1.2000000000000p+2"})
        self.assertEqual(manifest["room_exact"]["height"],
                         {"type": "int", "value": 4})
        self.assertEqual(manifest["room"]["width"], 4.5)
        self.assertEqual(manifest["room"]["height"], 4.0)

        # Reopen without a Room file or manual Room object; no new writes.
        resumed = invoke(
            self.db, "--room-numeric-contract", "typed_v1",
            "--resume-typed-room", "--expected-journal-head",
            created["journal_head"], "--developmental-policy", "local_cell_v2",
            "--exploration-mode", "goal_free")
        self.assertEqual(resumed.returncode, 0, resumed.stderr[-1100:])
        restored = json.loads(resumed.stdout)
        self.assertEqual(restored["tick"], 25)
        self.assertEqual(restored["journal_head"], created["journal_head"])
        self.assertEqual(restored["world_revision"], created["world_revision"])
        self.assertEqual(restored["manifest_sha256"], created["manifest_sha256"])
        self.assertEqual(_stored(self.db), saved)

        resumed_for_real = invoke(
            self.db, "--room-numeric-contract", "typed_v1",
            "--resume-typed-room", "--expected-journal-head",
            created["journal_head"], "--developmental-policy", "local_cell_v2",
            "--exploration-mode", "goal_free", "--ticks", "3")
        self.assertEqual(resumed_for_real.returncode, 0, resumed_for_real.stderr[-1100:])
        next_checkpoint = json.loads(resumed_for_real.stdout)
        self.assertEqual(next_checkpoint["tick"], 28)
        self.assertEqual(len(_stored(self.db)), 29)
        self.assertNotEqual(next_checkpoint["journal_head"], created["journal_head"])

    def test_missing_typed_optin_and_existing_db_are_rejected_before_append(self):
        denied = invoke(
            self.db, "--new-room-contract", str(EXAMPLE), "--ticks", "0")
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("--room-numeric-contract typed_v1", denied.stderr)
        self.assertFalse(self.db.exists())
        valid = invoke(self.db, *new_args(EXAMPLE, ticks=3))
        self.assertEqual(valid.returncode, 0, valid.stderr[-1000:])
        before = _stored(self.db)
        denied_existing = invoke(self.db, *new_args(EXAMPLE, ticks=6))
        self.assertNotEqual(denied_existing.returncode, 0)
        self.assertIn("refuses an existing SQLite", denied_existing.stderr)
        self.assertEqual(_stored(self.db), before)
        both = invoke(
            self.db, "--new-room-contract", str(EXAMPLE),
            "--resume-typed-room", "--room-numeric-contract", "typed_v1",
            "--expected-journal-head", before[-1][2],
            "--developmental-policy", "local_cell_v2",
            "--exploration-mode", "goal_free", "--ticks", "0")
        self.assertNotEqual(both.returncode, 0)
        self.assertIn("mutually exclusive", both.stderr)
        self.assertEqual(_stored(self.db), before)

    def test_ambiguous_and_oversized_json_fail_without_birthing_db(self):
        source = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        variants = {
            "dup": '{"schema":"evil",' + EXAMPLE.read_text(encoding="utf-8").lstrip()[1:],
            "nan": EXAMPLE.read_text(encoding="utf-8").replace(
                '"type": "int", "value": 4', '"type": "int", "value": NaN'),
            "bad_version": json.dumps(dict(source, schema="unexpected-v2")),
            "bad_bool": json.dumps(dict(source, height={"type": "int", "value": True})),
            "missing": None,
            "huge": " " * (1_048_576 + 1),
            "garbled": "}" * 8,
        }
        for label, raw in variants.items():
            with self.subTest(case=label):
                case_path = self.root / (label + ".json")
                db_path = self.root / (label + ".sqlite3")
                if raw is not None:
                    case_path.write_text(raw, encoding="utf-8")
                bad = invoke(db_path, *new_args(case_path, ticks=1))
                self.assertNotEqual(bad.returncode, 0)
                self.assertFalse(db_path.exists())
        self.assertFalse(self.db.exists())

    def test_concurrent_creators_are_exclusive_and_cannot_overwrite(self):
        command = [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.db), *new_args(EXAMPLE, ticks=8),
        ]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        workers = [
            subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        reports = [worker.communicate(timeout=35) for worker in workers]
        codes = [worker.returncode for worker in workers]
        self.assertEqual(sorted(codes), [0, 2], (codes, reports))
        winner = reports[codes.index(0)][0]
        self.assertEqual(json.loads(winner)["stored_events"], 9)
        self.assertEqual(len(_stored(self.db)), 9)
        self.assertTrue(
            ("existing SQLite" in reports[codes.index(2)][1]
             or "destination already exists" in reports[codes.index(2)][1]),
            reports[codes.index(2)][1],
        )

    def test_symlink_source_and_existing_symlink_target_fail_closed(self):
        definition = self.root / "symlink-room.json"
        try:
            definition.symlink_to(EXAMPLE)
        except OSError:
            self.skipTest("host does not allow symlinks")
        bad = invoke(self.db, *new_args(definition, ticks=1))
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse(self.db.exists())
        # Even if the target symlink points to an unrelated file,
        # the opt-in CLI must not follow it or truncate its contents.
        private_other = self.root / "private-other.sqlite3"
        private_other.write_bytes(b"do-not-touch-existing-private-file")
        self.db.symlink_to(private_other)
        invalid = invoke(self.db, *new_args(EXAMPLE, ticks=1))
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(private_other.read_bytes(),
                         b"do-not-touch-existing-private-file")

    def test_zero_tick_new_room_is_valid_versioned_bootstrap(self):
        created = invoke(self.db, *new_args(EXAMPLE, ticks=0))
        self.assertEqual(created.returncode, 0, created.stderr[-950:])
        events = _stored(self.db)
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0][1])["payload"]["schema"],
                         "mojum.aquarium.bootstrap/2")


if __name__ == "__main__":
    unittest.main()
