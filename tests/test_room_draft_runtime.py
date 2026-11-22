"""Offline room draft -> Aquarium CLI integration and fail-closed boot tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.noosphere import LocalNoosphere
from mojum.room_draft_runtime import load_runtime_room
from mojum.room_spec import MAX_INPUT_BYTES, RoomSpecError


VALID = (
    '{"schema":"mojum.world.point-room-draft/1",'
    '"width":4,"height":4,"obstacles":[[2,1,0.1,2]],"start":[1,2]}'
)


class RoomDraftRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.path = self.dir / "room.json"
        self.path.write_text(VALID, encoding="utf-8")

    def test_valid_draft_loads_as_real_runtime_geometry(self):
        room, start = load_runtime_room(self.path)
        self.assertEqual((room.width, room.height), (4.0, 4.0))
        self.assertEqual(room.obstacles, ((2.0, 1.0, 0.1, 2.0),))
        self.assertEqual(start, (1.0, 2.0))

    def test_draft_requires_explicit_schema(self):
        self.path.write_text(VALID.replace(
            '"schema":"mojum.world.point-room-draft/1",', ""), encoding="utf-8")
        with self.assertRaisesRegex(RoomSpecError, "requires schema"):
            load_runtime_room(self.path)

    def test_duplicate_keys_and_blocked_start_fail(self):
        self.path.write_text(VALID.replace('"width":4', '"width":4,"width":4'),
                             encoding="utf-8")
        with self.assertRaisesRegex(RoomSpecError, "duplicate JSON key"):
            load_runtime_room(self.path)
        self.path.write_text(VALID.replace('"start":[1,2]', '"start":[2,2]'),
                             encoding="utf-8")
        with self.assertRaisesRegex(RoomSpecError, "start lies inside"):
            load_runtime_room(self.path)

    def test_size_limit_and_unavailable_file_fail(self):
        self.path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
        with self.assertRaisesRegex(RoomSpecError, "byte limit"):
            load_runtime_room(self.path)
        with self.assertRaisesRegex(RoomSpecError, "cannot read room draft"):
            load_runtime_room(self.dir / "absent.json")

    def test_missing_nonblock_fails_before_open(self):
        from unittest.mock import patch

        with patch("mojum.room_draft_runtime.os.O_NONBLOCK", 0, create=True), \
             patch("mojum.room_draft_runtime.os.open") as mocked_open, \
             patch("mojum.room_draft_runtime.os.name", "posix"):
            with self.assertRaisesRegex(RoomSpecError, "without O_NONBLOCK"):
                load_runtime_room(self.path)
            mocked_open.assert_not_called()

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0),
                         "POSIX FIFO support required")
    def test_named_pipe_is_rejected_without_blocking(self):
        # A normal Path.open('rb') hangs here waiting for a FIFO writer.
        # Keep the regression in a child process with a hard timeout so that
        # a future regression fails this test rather than wedging the CI job.
        fifo = self.dir / "room.fifo"
        os.mkfifo(fifo)
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src") + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        command = [
            sys.executable, "-c",
            "import sys; from mojum.room_draft_runtime import load_runtime_room; "
            "load_runtime_room(sys.argv[1])", str(fifo),
        ]
        run = subprocess.run(command, cwd=root, env=env, text=True,
                             capture_output=True, check=False, timeout=5)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("room draft must be a regular file", run.stderr)

    @unittest.skipUnless(getattr(os, "O_NOFOLLOW", 0),
                         "platform has no O_NOFOLLOW")
    def test_room_file_symlink_is_rejected(self):
        linked = self.dir / "room-link.json"
        linked.symlink_to(self.path)
        with self.assertRaisesRegex(RoomSpecError, "cannot read room draft"):
            load_runtime_room(linked)

    def _cli(self, db, *, room_file=True, ticks=1):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src") + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
               "--ticks", str(ticks), "--neural-mode", "disabled"]
        if room_file:
            cmd += ["--room-file", str(self.path)]
        return subprocess.run(cmd, cwd=root, env=env, text=True,
                              capture_output=True, check=False)

    def test_custom_room_cold_reopen_pins_geometry_and_rejects_mismatch(self):
        db = self.dir / "room_PRIVATE.sqlite3"
        first = self._cli(db)
        self.assertEqual(first.returncode, 0, first.stderr)
        checkpoint = json.loads(first.stdout)
        self.assertEqual((checkpoint["tick"], checkpoint["stored_events"]), (1, 2))
        second = self._cli(db)
        self.assertEqual(second.returncode, 0, second.stderr)
        continued = json.loads(second.stdout)
        self.assertEqual((continued["tick"], continued["stored_events"]), (2, 3))
        with LocalNoosphere(db, "dante-fixture-001", read_only=True) as store:
            saved_head = store.head()

        # Missing or changed authored room must not silently fall back to
        # built-in geometry or append into an existing organism history.
        omitted = self._cli(db, room_file=False)
        self.assertNotEqual(omitted.returncode, 0)
        self.path.write_text(VALID.replace('"width":4', '"width":5'),
                             encoding="utf-8")
        changed = self._cli(db)
        self.assertNotEqual(changed.returncode, 0)
        with LocalNoosphere(db, "dante-fixture-001", read_only=True) as store:
            self.assertEqual(store.head(), saved_head)
            self.assertEqual(store.count(), 3)

    def test_invalid_room_does_not_create_journal(self):
        db = self.dir / "should-not-exist.sqlite3"
        self.path.write_text(VALID.replace('"start":[1,2]', '"start":[2,2]'),
                             encoding="utf-8")
        result = self._cli(db)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(db.exists())


    def test_valid_draft_over_noosphere_event_cap_fails_before_sqlite_birth(self):
        # A 256 KiB-valid room can produce a bootstrap event beyond the
        # Noosphere 128,000-byte limit after the room is materialized.
        from mojum.room_spec import validate_room_bytes

        doc = json.loads(VALID)
        doc.update(width=10, height=10, start=[0, 0],
                   obstacles=[[2.123456789, 1.123456789,
                               0.0123456789, 0.0123456789]] * 3000)
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(raw), MAX_INPUT_BYTES)
        self.assertTrue(validate_room_bytes(raw).valid)
        self.path.write_bytes(raw)
        room, start = load_runtime_room(self.path)
        self.assertEqual(len(room.obstacles), 3000)
        self.assertEqual(start, (0.0, 0.0))

        db = self.dir / "oversized_bootstrap_PRIVATE.sqlite3"
        result = self._cli(db)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("event exceeds maximum size", result.stderr)
        self.assertFalse(db.exists(), "failed bootstrap must not leave a journal")


if __name__ == "__main__":
    unittest.main()
