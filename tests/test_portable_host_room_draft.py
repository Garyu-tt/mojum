"""Offline end-to-end custom room admission in the bounded Aquarium host."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium_cold_copy import export_cold_copy, verify_cold_copy
from mojum.aquarium_trace_export import export_private_trace
from mojum.noosphere import LocalNoosphere
from mojum.portable_host import main as host_main
from mojum.room_draft_runtime import load_runtime_room
from mojum.room_spec import MAX_INPUT_BYTES
from mojum.sensory_play import ToyScene


ROOT = Path(__file__).resolve().parents[1]
ROOM = (
    '{"schema":"mojum.world.point-room-draft/1",'
    '"width":5,"height":4,"obstacles":[[2,1,0.1,2]],"start":[1,2]}'
)


class PortableHostCustomRoomTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "habitat_PRIVATE.sqlite3"
        self.room = self.root / "room.json"
        self.room.write_text(ROOM, encoding="utf-8")
        self.env = dict(os.environ)
        self.env["PYTHONPATH"] = str(ROOT / "src") + (
            os.pathsep + self.env["PYTHONPATH"]
            if self.env.get("PYTHONPATH") else ""
        )

    def invoke(self, *extra, include_room=True):
        command = [
            sys.executable, "-m", "mojum.portable_host",
            "--db", str(self.db), "--ticks", "2", "--interval-ms", "20",
            "--neural-mode", "disabled",
        ]
        if include_room:
            command.extend(("--room-file", str(self.room)))
        command.extend(extra)
        return subprocess.run(
            command, cwd=ROOT, env=self.env,
            capture_output=True, text=True, timeout=20, check=False,
        )

    def committed(self):
        with LocalNoosphere(
            self.db, "dante-fixture-001", read_only=True
        ) as journal:
            return journal.count(), journal.head()

    def test_custom_room_is_pinned_across_two_paced_starts(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        status = json.loads(first.stdout)
        self.assertEqual(
            (status["start_tick"], status["end_tick"],
             status["journal_events"]), (0, 2, 3),
        )
        self.assertEqual(status["schema"], "mojum.portable-host.run/2")
        self.assertNotIn(str(self.room), first.stdout)
        self.assertNotIn("obstacles", first.stdout)

        with LocalNoosphere(
            self.db, "dante-fixture-001", read_only=True
        ) as journal:
            event = journal.dataset(
                include_nonpublic=True, limit=3
            )["projection"]["items"][0]["event"]
            self.assertEqual(event["payload"]["room"]["width"], 5.0)
            self.assertEqual(event["payload"]["room"]["start"], [1.0, 2.0])
            self.assertEqual(event["payload"]["sensory_scene"]["toys"], [])

        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stderr)
        result = json.loads(second.stdout)
        self.assertEqual(
            (result["start_tick"], result["end_tick"],
             result["journal_events"]), (2, 4, 5),
        )
        self.assertNotEqual(result["journal_head"], status["journal_head"])

    def test_reopen_without_or_with_mutated_room_fails_without_append(self):
        self.assertEqual(self.invoke().returncode, 0)
        before = self.committed()

        no_draft = self.invoke(include_room=False)
        self.assertEqual(no_draft.returncode, 2)
        self.assertEqual(no_draft.stdout, "")
        self.assertEqual(self.committed(), before)

        self.room.write_text(ROOM.replace('"width":5', '"width":6'),
                             encoding="utf-8")
        changed = self.invoke()
        self.assertEqual(changed.returncode, 2)
        self.assertEqual(changed.stdout, "")
        self.assertEqual(self.committed(), before)

    def test_signal_stop_preserves_custom_room_for_cold_restart(self):
        def stop_during_wait(_delay):
            signal.raise_signal(signal.SIGTERM)

        with (
            patch("mojum.portable_host.time.sleep", side_effect=stop_during_wait),
            redirect_stdout(StringIO()),
        ):
            status = host_main([
                "--db", str(self.db), "--room-file", str(self.room),
                "--ticks", "2", "--interval-ms", "500",
                "--neural-mode", "disabled",
            ])

        self.assertEqual(status["stop_reason"], "signal")
        self.assertEqual(status["committed_ticks"], 1)
        self.assertEqual(self.committed(), (2, status["journal_head"]))
        restarted = self.invoke()
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        resumed = json.loads(restarted.stdout)
        self.assertEqual((resumed["start_tick"], resumed["end_tick"]), (1, 3))

    def test_custom_scene_replays_through_trace_and_cold_copy_apis(self):
        run = self.invoke()
        self.assertEqual(run.returncode, 0, run.stderr)
        status = json.loads(run.stdout)
        source_bytes = self.db.read_bytes()
        room, start = load_runtime_room(self.room)
        options = dict(
            room=room, start=start, neural_mode="disabled",
            motor_mode="developmental_fixture", sensory_mode="toy_probe",
            temporal_mode="range_guided",
            sensory_scene=ToyScene(width=room.width, height=room.height, toys=()),
        )
        data = ROOT / "src" / "mojum" / "data"
        seed = json.loads((data / "genesis_seed.json").read_text(encoding="utf-8"))
        graph = json.loads((data / "first_loop.json").read_text(encoding="utf-8"))
        trace = export_private_trace(
            self.db, self.root / "trace.private.jsonl", seed, graph,
            allow_private_export=True,
            expected_journal_head=status["journal_head"], **options,
        )
        self.assertEqual((trace["frames"], trace["journal_head"]),
                         (2, status["journal_head"]))
        backup_path = self.root / "backup.private.sqlite3"
        backup = export_cold_copy(self.db, backup_path, seed, graph, **options)
        verified = verify_cold_copy(
            backup_path, seed, graph,
            expected_agent_id=seed["genome_id"],
            expected_journal_head=status["journal_head"],
            expected_sha256=backup["db_sha256"], expected_tick=2, **options,
        )
        self.assertEqual((verified["tick"], verified["journal_head"]),
                         (2, status["journal_head"]))
        self.assertEqual(self.db.read_bytes(), source_bytes)

    def test_invalid_or_unavailable_room_does_not_create_journal(self):
        invalid = [
            ("absent", None),
            ("duplicates", ROOM.replace('"width":5', '"width":5,"width":5')),
            ("blocked_start", ROOM.replace('"start":[1,2]', '"start":[2,2]')),
            ("missing_schema", ROOM.replace(
                '"schema":"mojum.world.point-room-draft/1",', ""
            )),
            ("too_large", " " * (MAX_INPUT_BYTES + 1)),
        ]
        for name, text in invalid:
            with self.subTest(name=name):
                if text is None:
                    self.room.unlink(missing_ok=True)
                else:
                    self.room.write_text(text, encoding="utf-8")
                rejected = self.invoke()
                self.assertEqual(rejected.returncode, 2, rejected.stderr)
                self.assertEqual(rejected.stdout, "")
                self.assertFalse(self.db.exists(), name)

    def test_local_cell_v4_refuses_custom_room_before_journal_birth(self):
        run = self.invoke(
            "--motor-mode", "local_cell_v4",
            "--sensory-mode", "none", "--temporal-mode", "none",
        )
        self.assertEqual(run.returncode, 2)
        self.assertIn("--room-file is unsupported", run.stderr)
        self.assertFalse(self.db.exists())

    def test_default_host_remains_backward_compatible(self):
        without_draft = self.invoke(include_room=False)
        self.assertEqual(without_draft.returncode, 0, without_draft.stderr)
        status = json.loads(without_draft.stdout)
        self.assertEqual(status["schema"], "mojum.portable-host.run/2")
        self.assertEqual((status["end_tick"], status["journal_events"]), (2, 3))
        self.assertNotIn("room", status)
        pinned = self.committed()

        with_draft = self.invoke()
        self.assertEqual(with_draft.returncode, 2)
        self.assertEqual(self.committed(), pinned)


if __name__ == "__main__":
    unittest.main()
