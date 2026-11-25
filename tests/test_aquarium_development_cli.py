"""Real journal lifecycle operations through source and installed interpreters."""
from contextlib import closing, redirect_stderr, redirect_stdout
import copy
from importlib.resources import files
import io
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

from mojum.aquarium import Aquarium, load_cli_room
from mojum.aquarium_development import SCHEMA, main
from mojum.body_genetics import CHROMOSOME_SCHEMA, IDENTITY_SCHEMA
from mojum.noosphere import LocalNoosphere
from mojum.sensory_play import ToyScene
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))


class AquariumDevelopmentCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "organism.sqlite3"
        self.env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONIOENCODING": "ascii:strict"}

    def run_module(self, module, *args):
        # Running this file with installed `python -I` also isolates every CLI
        # child, so the exact same real-journal tests exercise wheel contents.
        isolation = ["-I"] if sys.flags.isolated else []
        return subprocess.run([sys.executable, *isolation, "-m", module, *map(str, args)],
                              cwd=self.root, env=self.env, capture_output=True, timeout=25)

    def run_cli(self, operation, *args, db=None):
        return self.run_module("mojum.aquarium_development", operation, "--db",
                               self.db if db is None else db, *args)

    def birth(self, *, db=None, seed=SEED, graph=GRAPH, ticks=2, **options):
        with Aquarium(self.db if db is None else db, seed, graph,
                      agent_id=seed["genome_id"], **options) as aquarium:
            for _ in range(ticks):
                aquarium.step()
            return aquarium.checkpoint()

    def snapshot(self, **options):
        with Aquarium(self.db, SEED, GRAPH, read_only=True, **options) as aquarium:
            return aquarium.checkpoint()

    def inventory(self):
        result = {}
        for path in self.root.iterdir():
            mode = path.lstat().st_mode
            data = path.read_bytes() if stat.S_ISREG(mode) else None
            if stat.S_ISLNK(mode):
                data = os.readlink(path)
            result[path.name] = (mode, data)
        return result

    def quiescent_delete_mode(self, path=None):
        # Exact directory/byte-preservation assertions use a closed fixture.
        # A separate live-WAL test verifies fresh committed pages and allows
        # SQLite's ordinary shared-memory reader coordination.
        with closing(sqlite3.connect(self.db if path is None else path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")

    def assert_rejected(self, result, *, code=2):
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)

    def receipt(self, result, operation, checkpoint, events):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report, {
            "schema": SCHEMA, "operation": operation,
            "tick": checkpoint["tick"], "stored_events": events,
            "development_months": checkpoint["development_months"],
            "journal_head": checkpoint["journal_head"],
            "manifest_sha256": checkpoint["manifest_sha256"],
        })
        self.assertNotIn(str(self.root).encode(), result.stdout)
        return report

    def test_status_is_read_only_and_reports_only_metadata(self):
        self.birth()
        with Aquarium(self.db, SEED, GRAPH) as aquarium:
            aquarium.assign_human_name({
                "schema": IDENTITY_SCHEMA, "agent_id": SEED["genome_id"],
                "display_name": "Private display name", "assigned_by": "human",
            }, actor_id="private-naming-actor")
            before = aquarium.checkpoint()
        self.quiescent_delete_mode()
        inventory = self.inventory()
        result = self.run_cli("status")
        self.receipt(result, "status", before, 4)
        self.assertNotIn(b"Private display name", result.stdout)
        self.assertNotIn(b"private-naming-actor", result.stdout)
        self.assertEqual(self.inventory(), inventory)

    def test_status_sees_current_committed_wal_without_changing_history(self):
        with Aquarium(self.db, SEED, GRAPH) as writer:
            writer.step()
            older = writer.checkpoint()
            writer.advance_development(3)
            before = writer.checkpoint()
            wal = Path(str(self.db) + "-wal")
            self.assertTrue(wal.is_file())
            self.assertGreater(wal.stat().st_size, 0)
            main_bytes, wal_bytes = self.db.read_bytes(), wal.read_bytes()
            self.receipt(self.run_cli("status", "--expected-head", before["journal_head"]),
                         "status", before, 3)
            self.assert_rejected(self.run_cli("status", "--expected-head", older["journal_head"]))
            self.assertEqual(self.db.read_bytes(), main_bytes)
            self.assertEqual(wal.read_bytes(), wal_bytes)
            self.assertEqual(writer.checkpoint(), before)

    def test_advance_changes_one_lifecycle_event_then_normal_runtime_resumes(self):
        before = self.birth()
        somatic = {
            "schema": CHROMOSOME_SCHEMA, "genome_id": SEED["genome_id"],
            "inherited": {"height_bias_milli": 1000, "head_ratio_bias_milli": 1000,
                          "leg_ratio_bias_milli": 1000, "shoulder_ratio_bias_milli": 1000},
        }
        with Aquarium(self.db, SEED, GRAPH, read_only=True) as aquarium:
            height_before = aquarium.body_preview(somatic)["dimensions"]["height_mm"]
        result = self.run_cli("advance", "--months", 6, "--expected-head", before["journal_head"],
                              "--reason", "synthetic semester \u661f")
        after = self.snapshot()
        self.receipt(result, "advance", after, 4)
        self.assertEqual(after["development_months"], 78)
        self.assertNotEqual(after["journal_head"], before["journal_head"])
        for key in ("tick", "sim_time_ns", "world_revision", "genome_revision",
                    "neural_state", "manifest_sha256"):
            self.assertEqual(after[key], before[key], key)
        with Aquarium(self.db, SEED, GRAPH, read_only=True) as aquarium:
            self.assertGreater(aquarium.body_preview(somatic)["dimensions"]["height_mm"], height_before)
        with closing(sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True)) as db:
            wire = json.loads(db.execute("SELECT wire_json FROM events WHERE seq = 4").fetchone()[0])
        self.assertEqual(wire["topic"], "development.advance")
        self.assertEqual(wire["payload"]["reason"], "synthetic semester \u661f")
        self.assertNotIn(b"semester", result.stdout)
        resumed = self.run_module("mojum.aquarium", "--db", self.db, "--ticks", 1,
                                  "--expected-head", after["journal_head"])
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        state = json.loads(resumed.stdout)
        self.assertEqual((state["tick"], state["stored_events"], state["development_months"]), (3, 5, 78))

    def test_stale_anchor_cannot_repeat_an_advance(self):
        before = self.birth()
        arguments = ("--months", 2, "--expected-head", before["journal_head"])
        self.assertEqual(self.run_cli("advance", *arguments).returncode, 0)
        self.quiescent_delete_mode()
        inventory = self.inventory()
        self.assert_rejected(self.run_cli("advance", *arguments))
        self.assert_rejected(self.run_cli("status", "--expected-head", before["journal_head"]))
        self.assertEqual(self.inventory(), inventory)
        self.assertEqual(self.snapshot()["development_months"], 74)

    def test_missing_empty_foreign_and_invalid_files_are_never_adopted(self):
        empty = self.root / "empty.sqlite3"
        empty.touch()
        foreign = self.root / "foreign.sqlite3"
        with closing(sqlite3.connect(foreign)) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
            db.execute("INSERT INTO unrelated VALUES ('preserve me')")
            db.commit()
        invalid = self.root / "invalid.sqlite3"
        invalid.write_bytes(b"not an organism or SQLite file")
        bootstrapless = self.root / "bootstrapless.sqlite3"
        with LocalNoosphere(bootstrapless, SEED["genome_id"]):
            pass
        self.quiescent_delete_mode(bootstrapless)
        missing = self.root / "missing.sqlite3"
        for path in (missing, empty, foreign, invalid, bootstrapless):
            for operation in ("status", "advance"):
                with self.subTest(path=path.name, operation=operation):
                    inventory = self.inventory()
                    args = () if operation == "status" else ("--months", 1, "--expected-head", "0" * 64)
                    self.assert_rejected(self.run_cli(operation, *args, db=path))
                    self.assertEqual(self.inventory(), inventory)

    def test_directories_symlinks_and_fifo_are_rejected_promptly(self):
        before = self.birth()
        directory = self.root / "directory"
        directory.mkdir()
        targets = [directory]
        for name, target in (("existing-link", self.db), ("dangling-link", self.root / "missing")):
            link = self.root / name
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                continue
            targets.append(link)
        if hasattr(os, "mkfifo"):
            fifo = self.root / "pipe"
            os.mkfifo(fifo)
            targets.append(fifo)
        for target in targets:
            with self.subTest(target=target.name):
                inventory = self.inventory()
                self.assert_rejected(self.run_cli("status", db=target))
                self.assert_rejected(self.run_cli("advance", "--months", 1,
                                                 "--expected-head", before["journal_head"], db=target))
                self.assertEqual(self.inventory(), inventory)

    def test_invalid_requests_fail_without_writable_open(self):
        before = self.birth()
        anchor = ("--expected-head", before["journal_head"])
        cases = [(), ("--months", 1), ("--months", 0, *anchor), ("--months", 13, *anchor),
                 ("--months", 1, "--expected-head", "not-a-head"),
                 ("--months", 1, "--expected-head", "A" * 64)]
        cases.extend(("--months", 1, *anchor, "--reason", reason)
                     for reason in ("", " ", "line\nbreak", "invisible\u200b", "a" * 257))
        inventory = self.inventory()
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assert_rejected(self.run_cli("advance", *arguments))
                self.assertEqual(self.inventory(), inventory)
        self.assertEqual(self.snapshot(), before)

    def test_maximum_age_is_reported_but_cannot_be_advanced(self):
        self.birth()
        with Aquarium(self.db, SEED, GRAPH) as aquarium:
            for _ in range(10):
                aquarium.advance_development(12)
            before = aquarium.checkpoint()
        self.quiescent_delete_mode()
        self.receipt(self.run_cli("status"), "status", before, 13)
        inventory = self.inventory()
        self.assert_rejected(self.run_cli("advance", "--months", 1,
                                         "--expected-head", before["journal_head"]))
        self.assertEqual(self.inventory(), inventory)

    def test_custom_genome_graph_and_ascii_console_roundtrip(self):
        seed = copy.deepcopy(SEED)
        seed["genome_id"] = "development-\u661f"
        graph = copy.deepcopy(GRAPH)
        graph["id"] = "development-custom-graph"
        genome_path, graph_path = self.root / "seed.json", self.root / "graph.json"
        genome_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        graph_path.write_text(json.dumps(graph), encoding="utf-8")
        before = self.birth(seed=seed, graph=graph, neural_mode="disabled")
        options = ("--genome", genome_path, "--graph", graph_path, "--neural-mode", "disabled")
        self.receipt(self.run_cli("status", *options), "status", before, 3)
        result = self.run_cli("advance", *options, "--months", 1,
                              "--expected-head", before["journal_head"])
        with Aquarium(self.db, seed, graph, agent_id=seed["genome_id"],
                      neural_mode="disabled", read_only=True) as aquarium:
            self.receipt(result, "advance", aquarium.checkpoint(), 4)

    def test_custom_room_and_developmental_controller_resume(self):
        room_file = self.root / "room.json"
        room_file.write_text(json.dumps({
            "schema": "mojum.world.point-room-draft/1",
            "width": 5, "height": 4, "start": [1.5, 2.5], "obstacles": [],
        }), encoding="utf-8")
        room, start = load_cli_room(room_file, "developmental_fixture")
        options = {"room": room, "start": start, "motor_mode": "developmental_fixture",
                   "brain_mode": "developmental_controller", "neural_mode": "disabled"}
        before = self.birth(**options)
        flags = ("--room-file", room_file, "--motor-mode", "developmental_fixture",
                 "--brain-mode", "developmental_controller", "--neural-mode", "disabled")
        result = self.run_cli("advance", *flags, "--months", 3,
                              "--expected-head", before["journal_head"])
        after = self.snapshot(**options)
        self.receipt(result, "advance", after, 4)
        for key in ("tick", "world_revision", "motor_state"):
            self.assertEqual(before[key], after[key])
        inventory = self.inventory()
        self.assert_rejected(self.run_cli("advance", "--months", 1,
                                         "--expected-head", after["journal_head"]))
        self.assertEqual(self.inventory(), inventory)
        resumed = self.run_module("mojum.aquarium", "--db", self.db, "--ticks", 1,
                                  *flags, "--expected-head", after["journal_head"])
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["development_months"], 75)

    def test_default_contact_scene_and_temporal_state_are_preserved(self):
        options = {"motor_mode": "developmental_fixture", "sensory_mode": "toy_probe",
                   "temporal_mode": "range_guided"}
        before = self.birth(**options)
        result = self.run_cli("advance", "--months", 1, "--expected-head", before["journal_head"],
                              "--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                              "--temporal-mode", "range_guided")
        after = self.snapshot(**options)
        self.receipt(result, "advance", after, 4)
        for key in ("tick", "motor_state", "toy_contact_state", "temporal_range_state"):
            self.assertEqual(before[key], after[key])

    def test_custom_scene_is_rejected_instead_of_inferred(self):
        for width in (4, 5):
            with self.subTest(width=width):
                self.db = self.root / f"scene-{width}.sqlite3"
                room_file = self.root / f"scene-{width}.json"
                room_file.write_text(json.dumps({
                    "schema": "mojum.world.point-room-draft/1", "width": width,
                    "height": 4, "start": [1, 2], "obstacles": [],
                }), encoding="utf-8")
                options = {"motor_mode": "developmental_fixture", "sensory_mode": "toy_probe",
                           "room": Room(width, 4, ()), "start": (1, 2),
                           "sensory_scene": ToyScene(width=width, height=4, toys=())}
                before = self.birth(**options)
                self.quiescent_delete_mode()
                inventory = self.inventory()
                flags = ("--room-file", room_file, "--motor-mode", "developmental_fixture",
                         "--sensory-mode", "toy_probe")
                self.assert_rejected(self.run_cli("status", *flags))
                self.assert_rejected(self.run_cli("advance", *flags, "--months", 1,
                                                 "--expected-head", before["journal_head"]))
                self.assertEqual(self.inventory(), inventory)

    def test_local_cell_status_works_but_development_events_remain_forbidden(self):
        before = self.birth(motor_mode="local_cell_v4", neural_mode="disabled")
        self.quiescent_delete_mode()
        flags = ("--motor-mode", "local_cell_v4", "--neural-mode", "disabled")
        self.receipt(self.run_cli("status", *flags), "status", before, 3)
        inventory = self.inventory()
        self.assert_rejected(self.run_cli("advance", *flags, "--months", 1,
                                         "--expected-head", before["journal_head"]))
        self.assertEqual(self.inventory(), inventory)

    def test_bad_genome_and_graph_inputs_preserve_existing_journal(self):
        before = self.birth()
        input_file = self.root / "input.json"
        inputs = [
            ("--genome", b'{"genome_id":"first",' + json.dumps(SEED).encode()[1:]),
            ("--genome", json.dumps(SEED).encode() + b" " * (256 * 1024)),
            ("--genome", b"[" * 2000), ("--genome", b"\xff"),
            ("--graph", b'{"id":"first",' + json.dumps(GRAPH).encode()[1:]),
            ("--graph", b"{}"),
        ]
        for flag, data in inputs:
            with self.subTest(flag=flag, size=len(data)):
                input_file.write_bytes(data)
                inventory = self.inventory()
                self.assert_rejected(self.run_cli("advance", flag, input_file, "--months", 1,
                                                 "--expected-head", before["journal_head"]))
                self.assertEqual(self.inventory(), inventory)

    def test_concurrent_writer_before_or_after_writable_reopen_is_preserved(self):
        for moment in ("before-reopen", "after-reopen"):
            with self.subTest(moment=moment):
                self.db = self.root / (moment + ".sqlite3")
                before = self.birth()
                competing = []
                opened_read_only = []

                def factory(*args, **kwargs):
                    read_only = kwargs.get("read_only", False)
                    opened_read_only.append(read_only)
                    if read_only:
                        return Aquarium(*args, **kwargs)
                    current = Aquarium(*args, **kwargs) if moment == "after-reopen" else None
                    with Aquarium(self.db, SEED, GRAPH) as writer:
                        writer.step()
                        competing.append(writer.checkpoint())
                    return current if current is not None else Aquarium(*args, **kwargs)

                stdout, stderr = io.StringIO(), io.StringIO()
                with patch("mojum.aquarium_development.Aquarium", side_effect=factory):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as error:
                            main(["advance", "--db", str(self.db), "--months", "1",
                                  "--expected-head", before["journal_head"]])
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(opened_read_only, [True, False])
                self.assertEqual(self.snapshot(), competing[0])
                self.assertEqual(competing[0]["development_months"], 72)

    def test_exhausted_event_budget_is_rejected_before_writable_open(self):
        before = self.birth()
        self.quiescent_delete_mode()
        inventory = self.inventory()
        opened_read_only = []

        def factory(*args, **kwargs):
            opened_read_only.append(kwargs.get("read_only", False))
            return Aquarium(*args, **kwargs)

        with patch("mojum.aquarium.MAX_REPLAY_EVENTS", 3):
            with patch("mojum.aquarium_development.Aquarium", side_effect=factory):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main(["advance", "--db", str(self.db), "--months", "1",
                              "--expected-head", before["journal_head"]])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(opened_read_only, [True])
        self.assertEqual(self.inventory(), inventory)

    def test_locked_journal_reports_storage_failure_and_then_recovers(self):
        before = self.birth()
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            self.assert_rejected(self.run_cli("advance", "--months", 1,
                                             "--expected-head", before["journal_head"]), code=1)
        self.assertEqual(self.snapshot(), before)
        advanced = self.run_cli("advance", "--months", 1,
                                "--expected-head", before["journal_head"])
        self.receipt(advanced, "advance", self.snapshot(), 4)


if __name__ == "__main__":
    unittest.main()
