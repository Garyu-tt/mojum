"""Replay a journal created with an authored room through the public CLIs."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RoomReplayCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "source.sqlite3"
        self.backup = self.root / "backup.sqlite3"
        self.trace = self.root / "trace.jsonl"
        self.room_file = self.root / "room.json"
        self.room = {"schema": "mojum.world.point-room-draft/1", "width": 7,
                     "height": 3, "obstacles": [[2, 1, 0.1, 1]], "start": [5, 1]}
        self.room_file.write_text(json.dumps(self.room), encoding="utf-8")
        boot = self.cli("aquarium", "--ticks", "2", "--db", self.db,
                        "--room-file", self.room_file)
        self.assertEqual(boot.returncode, 0, boot.stderr)
        self.head = json.loads(boot.stdout)["journal_head"]
        self.export = ("aquarium_cold_copy", "export", "--db", self.db,
                       "--output", self.backup)
        self.verify = ("aquarium_cold_copy", "verify", "--db", self.backup,
                       "--expected-head", self.head, "--expected-tick", "2")
        self.export_trace = ("aquarium_trace_export", "--db", self.db,
                             "--output", self.trace, "--expected-head", self.head,
                             "--allow-private-export")

    def cli(self, module, *args):
        return subprocess.run(
            [sys.executable, "-m", "mojum." + module, *args], cwd=self.root,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), capture_output=True,
            encoding="utf-8", timeout=30,
        )

    def test_replay_commands_use_pinned_room_and_start(self):
        original = self.db.read_bytes()
        for command in (self.export, self.verify, self.export_trace):
            result = self.cli(*command, "--room-file", self.room_file)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["journal_head"], self.head)
        frames = [json.loads(line) for line in self.trace.read_text("utf-8").splitlines()]
        self.assertEqual(frames[-1]["trace_room"], {"width": 7, "height": 3})
        self.assertEqual([f["tick"] for f in frames[:-1]], [0, 1])
        self.assertGreater(frames[0]["world"]["position"][0], 4)
        self.assertEqual(self.db.read_bytes(), original)

    def test_changed_start_refuses_export_and_verification(self):
        result = self.cli(*self.export, "--room-file", self.room_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = self.db.read_bytes()
        saved = self.backup.read_bytes()
        self.room["start"] = [6, 1]
        self.room_file.write_text(json.dumps(self.room), encoding="utf-8")
        other = self.root / "other.sqlite3"
        for command in ((*self.export[:-1], other), self.verify, self.export_trace):
            with self.subTest(command=command[:2]):
                refused = self.cli(*command, "--room-file", self.room_file)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("mode mismatch", refused.stderr)
        self.assertFalse(self.trace.exists())
        self.assertFalse(other.exists())
        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(self.backup.read_bytes(), saved)
