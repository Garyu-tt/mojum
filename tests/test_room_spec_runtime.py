"""Typed-room draft -> CPU point-room runtime and an explicit PRIVATE journal.

The offline designer emits a lossless JSON draft. These tests pin the missing
bridge: a validated draft becomes a runtime ``Room``/start pair and can open a
new Aquarium journal without reinterpreting number tokens or any other
existing journal.
"""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import main, V2B_WIRE_MODE
from mojum.room_spec import (
    RoomSpecError,
    load_runtime_room,
    validate_room_bytes,
)
from mojum.world_room import Room, initial_state


def encode(document):
    return json.dumps(document).encode("utf-8")


CANONICAL = {
    "width": 4,
    "height": 4,
    "obstacles": [[2, 1, 0.1, 2]],
    "start": [1.0, 2.0],
}


class BuildRuntimeRoomTests(unittest.TestCase):
    def test_canonical_draft_builds_the_builtin_runtime_room(self):
        room, start = load_runtime_room(encode(CANONICAL))
        self.assertIsInstance(room, Room)
        self.assertEqual((room.width, room.height), (4, 4))
        self.assertEqual(room.obstacles, ((2, 1, 0.1, 2),))
        self.assertEqual(start, (1.0, 2.0))
        # The constructed start must itself be a valid runtime position.
        self.assertEqual(initial_state(room, *start).tick, 0)

    def test_number_token_spelling_is_preserved_for_the_runtime(self):
        raw = b'{"width":4e0,"height":4,"obstacles":[[2,1,1e-1,2]],"start":[1,2]}'
        self.assertTrue(validate_room_bytes(raw).valid)
        room, start = load_runtime_room(raw)
        # Integer tokens stay ints; exponent/fraction tokens become floats.
        self.assertEqual((type(room.width), room.width), (float, 4.0))
        self.assertEqual((type(room.obstacles[0][0]), room.obstacles[0][0]), (int, 2))
        self.assertEqual((type(room.obstacles[0][2]), room.obstacles[0][2]), (float, 0.1))
        self.assertEqual(start, (1.0, 2.0))

    def test_start_inside_a_closed_obstacle_is_rejected(self):
        document = dict(CANONICAL, start=[2.0, 1.5])
        with self.assertRaisesRegex(RoomSpecError, "start lies inside"):
            load_runtime_room(encode(document))

    def test_boolean_metres_are_not_accepted_as_geometry(self):
        document = dict(CANONICAL, width=True)
        with self.assertRaisesRegex(RoomSpecError, "width must be a JSON number"):
            load_runtime_room(encode(document))

    def test_geometry_the_float_runtime_cannot_represent_fails_closed(self):
        # 0.1 + 0.2 == 0.3 exactly in Decimal but not in binary floats; the
        # runtime Room would refuse it, so the bridge must refuse it first.
        raw = b'{"width":0.3,"height":1,"obstacles":[[0.1,0,0.2,0.1]],"start":[0,0.5]}'
        self.assertTrue(validate_room_bytes(raw).valid)
        with self.assertRaisesRegex(RoomSpecError, "not representable"):
            load_runtime_room(raw)

    def test_malformed_json_and_non_bytes_are_rejected(self):
        for raw in (b"{", b"[]"):
            with self.subTest(raw=raw):
                with self.assertRaises(RoomSpecError):
                    load_runtime_room(raw)
        with self.assertRaises(RoomSpecError):
            load_runtime_room('{"width":4}')  # not bytes


class AquariumRoomSpecCliTests(unittest.TestCase):
    def invoke(self, argv, stderr=None):
        target = io.StringIO() if stderr is None else stderr
        with patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(target):
            main()

    def test_draft_opens_and_cold_replays_a_private_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = tmp_path / "room-draft.sqlite3"
            spec = tmp_path / "room.json"
            spec.write_bytes(b'{"width":8,"height":4,"obstacles":[[3,0,1,4]],'
                             b'"start":[1,2]}')
            argv = ["mojum.aquarium", "--db", str(db), "--room-spec", str(spec),
                    "--ticks", "3", "--neural-mode", "disabled"]

            self.invoke(argv)
            with contextlib.closing(_connect(db)) as conn:
                rows = conn.execute(
                    "SELECT wire_json FROM events ORDER BY seq LIMIT 1").fetchall()
            bootstrap = json.loads(rows[0][0])
            self.assertEqual(bootstrap["payload"]["room"], {
                "width": 8.0, "height": 4.0,
                "obstacles": [[3, 0, 1, 4]], "start": [1.0, 2.0],
            })
            with contextlib.closing(_connect(db)) as conn:
                before = conn.execute(
                    "SELECT seq, event_hash FROM events ORDER BY seq").fetchall()

            # Reopening with the same draft must replay, then continue.
            self.invoke(argv)
            with contextlib.closing(_connect(db)) as conn:
                after = conn.execute(
                    "SELECT seq, event_hash FROM events ORDER BY seq").fetchall()
            self.assertEqual(after[:len(before)], before)
            self.assertGreater(len(after), len(before))

    def test_invalid_draft_fails_before_creating_a_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = tmp_path / "rejected.sqlite3"
            spec = tmp_path / "bad-room.json"
            # Start lies on a closed obstacle boundary.
            spec.write_bytes(b'{"width":4,"height":4,"obstacles":[[2,1,0.1,2]],'
                             b'"start":[2,1.5]}')
            stderr = io.StringIO()
            with self.assertRaises(SystemExit) as error:
                self.invoke(["mojum.aquarium", "--db", str(db),
                             "--room-spec", str(spec), "--ticks", "1"], stderr)
            self.assertEqual(error.exception.code, 2)
            self.assertIn("start lies inside", stderr.getvalue())
            self.assertFalse(db.exists())

    def test_unreadable_draft_is_a_command_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = tmp_path / "missing.sqlite3"
            with self.assertRaises(SystemExit) as error:
                self.invoke(["mojum.aquarium", "--db", str(db),
                             "--room-spec", str(tmp_path / "absent.json"),
                             "--ticks", "1"])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(db.exists())

    def test_local_cell_wire_modes_cannot_interpret_a_custom_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = tmp_path / "local-cell.sqlite3"
            spec = tmp_path / "room.json"
            spec.write_bytes(b'{"width":8,"height":4,"obstacles":[],"start":[1,2]}')
            stderr = io.StringIO()
            with self.assertRaises(SystemExit) as error:
                self.invoke(["mojum.aquarium", "--db", str(db),
                             "--room-spec", str(spec), "--ticks", "0",
                             "--motor-mode", V2B_WIRE_MODE], stderr)
            self.assertEqual(error.exception.code, 2)
            self.assertIn("local-cell", stderr.getvalue())
            self.assertFalse(db.exists())


def _connect(path):
    import sqlite3
    return sqlite3.connect(path)


if __name__ == "__main__":
    unittest.main()
