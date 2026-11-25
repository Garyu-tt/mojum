"""Replay real empty/default scene journals through the three export CLIs."""

from contextlib import closing
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_cold_copy import export_cold_copy
from mojum.room_draft_runtime import load_runtime_room
from mojum.sensory_play import ToyScene


ROOT = Path(__file__).resolve().parents[1]
DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))


class SensorySceneCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def journal(self, name="source", *, width=None, empty=True,
                sensory="toy_probe", neural="disabled"):
        room, start = None, (1.0, 2.0)
        args = ["--motor-mode", "developmental_fixture",
                "--sensory-mode", sensory, "--neural-mode", neural]
        if width is not None:
            room_file = self.root / (name + ".room.json")
            room_file.write_text(json.dumps({
                "schema": "mojum.world.point-room-draft/1",
                "width": width, "height": 4,
                "obstacles": [[2, 1, 0.1, 2]], "start": [1, 2],
            }), encoding="utf-8")
            room, start = load_runtime_room(room_file)
            args.extend(("--room-file", str(room_file)))
        temporal = "range_guided" if sensory == "toy_probe" else "none"
        args.extend(("--temporal-mode", temporal))
        scene = None
        if empty:
            scene = (ToyScene(toys=()) if room is None else
                     ToyScene(width=room.width, height=room.height, toys=()))
        options = dict(room=room, start=start, sensory_scene=scene,
                       motor_mode="developmental_fixture", sensory_mode=sensory,
                       neural_mode=neural, temporal_mode=temporal)
        source = self.root / (name + ".sqlite3")
        with Aquarium(source, SEED, GRAPH, **options) as aquarium:
            frames = [aquarium.step() for _ in range(3)]
            head = aquarium.checkpoint()["journal_head"]
        # Use a quiescent standalone input for exact sidecar-inventory checks.
        # SQLite may create synchronization sidecars when reading WAL-mode
        # sources; live-WAL backup behavior is covered by the exporter suites.
        with closing(sqlite3.connect(source)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=DELETE").fetchone(),
                             ("delete",))
        return dict(source=source, head=head, frames=frames, options=options,
                    args=args, width=4 if width is None else width, name=name)

    def cli(self, module, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "mojum." + module,
             *(str(arg) for arg in args)],
            cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
            capture_output=True, encoding="utf-8", timeout=30,
        )

    def assert_success(self, result, head):
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata["journal_head"], head)
        return metadata

    def directory_bytes(self):
        return {path.name: path.read_bytes() for path in self.root.iterdir()
                if path.is_file()}

    def roundtrip(self, fixture, selector, label="selected"):
        source = fixture["source"]
        source_bytes = source.read_bytes()
        args = [*fixture["args"], *selector]
        prefix = fixture["name"] + "-" + label
        trace = self.root / (prefix + ".jsonl")
        backup = self.root / (prefix + ".backup.sqlite3")
        exported = self.assert_success(self.cli(
            "aquarium_trace_export", "--db", source, "--output", trace,
            "--expected-head", fixture["head"], "--allow-private-export", *args,
        ), fixture["head"])
        self.assertEqual(exported["frames"], 3)
        self.assertEqual(exported["trace_sha256"], hashlib.sha256(trace.read_bytes()).hexdigest())
        rows = [json.loads(line) for line in trace.read_text("utf-8").splitlines()]
        self.assertEqual(rows[:-1], fixture["frames"])
        self.assertEqual(rows[-1]["trace_room"], {"width": fixture["width"], "height": 4})
        copied = self.assert_success(self.cli(
            "aquarium_cold_copy", "export", "--db", source,
            "--output", backup, *args,
        ), fixture["head"])
        self.assertEqual((copied["tick"], copied["event_count"]), (3, 4))
        self.assertEqual(copied["db_sha256"], hashlib.sha256(backup.read_bytes()).hexdigest())
        before_verify = self.directory_bytes()
        verified = self.assert_success(self.cli(
            "aquarium_cold_copy", "verify", "--db", backup,
            "--expected-head", fixture["head"], "--expected-tick", "3",
            "--expected-sha256", copied["db_sha256"], *args,
        ), fixture["head"])
        self.assertEqual(verified["tick"], 3)
        self.assertEqual(verified["db_sha256_before_replay"], copied["db_sha256"])
        self.assertEqual(self.directory_bytes(), before_verify)
        self.assertEqual(source.read_bytes(), source_bytes)
        for path in (source, backup):
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(path) + suffix).exists())
        return backup, copied

    def reject_all(self, fixture, selector, message):
        backup = self.root / (fixture["name"] + "-known.backup.sqlite3")
        copied = export_cold_copy(fixture["source"], backup, SEED, GRAPH,
                                  **fixture["options"])
        args = [*fixture["args"], *selector]
        commands = (
            ("aquarium_trace_export", "--db", fixture["source"],
             "--output", self.root / "refused.jsonl", "--allow-private-export",
             "--expected-head", fixture["head"]),
            ("aquarium_cold_copy", "export", "--db", fixture["source"],
             "--output", self.root / "refused.sqlite3"),
            ("aquarium_cold_copy", "verify", "--db", backup,
             "--expected-head", fixture["head"], "--expected-tick", "3",
             "--expected-sha256", copied["db_sha256"]),
        )
        before = self.directory_bytes()
        for command in commands:
            with self.subTest(command=command[:2]):
                result = self.cli(*command, *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn(message, result.stderr)
                self.assertEqual(self.directory_bytes(), before)

    def test_authored_empty_scenes_roundtrip_both_sensor_versions(self):
        for width, sensory, neural in ((5, "toy_probe", "disabled"),
                                       (4, "toy_probe_v2", "shadow")):
            with self.subTest(width=width, sensory=sensory, neural=neural):
                fixture = self.journal(sensory, width=width,
                                       sensory=sensory, neural=neural)
                self.roundtrip(fixture, ("--sensory-scene", "empty"))

    def test_explicit_empty_scene_also_replays_the_builtin_room(self):
        self.roundtrip(self.journal(sensory="toy_probe_v2"),
                       ("--sensory-scene", "empty"))

    def test_legacy_default_toys_keep_omitted_and_explicit_default_behavior(self):
        fixture = self.journal(empty=False, neural="shadow")
        self.roundtrip(fixture, (), "omitted")
        self.roundtrip(fixture, ("--sensory-scene", "default"), "explicit")

    def test_wrong_default_does_not_resize_an_authored_empty_scene(self):
        for index, selector in enumerate(((), ("--sensory-scene", "default"))):
            with self.subTest(selector=selector):
                self.reject_all(self.journal(str(index), width=5), selector,
                                "sensory and motor room dimensions differ")

    def test_matching_dimensions_do_not_bypass_empty_scene_manifest(self):
        for index, selector in enumerate(((), ("--sensory-scene", "default"))):
            with self.subTest(selector=selector):
                self.reject_all(self.journal(str(index), width=4), selector,
                                "mode mismatch")

    def test_empty_selector_cannot_rewrite_a_legacy_toy_scene(self):
        self.reject_all(self.journal(empty=False), ("--sensory-scene", "empty"),
                        "mode mismatch")

    def test_empty_requires_a_toy_sensory_mode(self):
        self.reject_all(self.journal(),
                        ("--sensory-scene", "empty", "--sensory-mode", "none"),
                        "--sensory-scene empty requires toy_probe or toy_probe_v2")

    def test_unknown_selector_is_rejected_without_filesystem_changes(self):
        self.reject_all(self.journal(), ("--sensory-scene", "infer"), "invalid choice")

    def test_empty_scene_keeps_room_identity_and_fixed_geometry_admission(self):
        fixture = self.journal("changed-start", width=5)
        changed_room = self.root / "changed-start.room.json"
        draft = json.loads(changed_room.read_text(encoding="utf-8"))
        draft["start"] = [1.25, 2]
        changed_room.write_text(json.dumps(draft), encoding="utf-8")
        self.reject_all(fixture, ("--sensory-scene", "empty"), "mode mismatch")

        self.reject_all(self.journal("fixed-geometry", width=5),
                        ("--sensory-scene", "empty", "--motor-mode", "local_cell_v4",
                         "--sensory-mode", "toy_probe_v2", "--temporal-mode", "none"),
                        "--room-file is incompatible with fixed-geometry local-cell modes")

    def test_private_opt_in_and_independent_anchors_remain_required(self):
        fixture = self.journal(width=5)
        backup, _ = self.roundtrip(fixture, ("--sensory-scene", "empty"))
        args = [*fixture["args"], "--sensory-scene", "empty"]
        trace_command = ("aquarium_trace_export", "--db", fixture["source"],
                         "--output", self.root / "refused.jsonl")
        verify_command = ("aquarium_cold_copy", "verify", "--db", backup)
        commands = (
            (*trace_command, "--expected-head", fixture["head"]),
            (*trace_command, "--allow-private-export", "--expected-head", "0" * 64),
            (*verify_command, "--expected-head", "0" * 64),
            (*verify_command, "--expected-head", fixture["head"], "--expected-tick", "4"),
            (*verify_command, "--expected-head", fixture["head"],
             "--expected-sha256", "0" * 64),
            verify_command,
        )
        before = self.directory_bytes()
        for command in commands:
            with self.subTest(command=command):
                result = self.cli(*command, *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.directory_bytes(), before)


if __name__ == "__main__":
    unittest.main()
