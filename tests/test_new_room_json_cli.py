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
