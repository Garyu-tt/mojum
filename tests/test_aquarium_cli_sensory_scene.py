"""Actual Aquarium/foreground-host interoperability for explicit toy scenes."""

from contextlib import closing
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import mojum
from mojum.aquarium import Aquarium, load_cli_room
from mojum.sensory_play import ToyScene


DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text("utf-8"))


class AquariumCliSensorySceneTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = dict(os.environ, PYTHONIOENCODING="ascii:strict",
                        PYTHONPATH=str(Path(mojum.__file__).resolve().parents[1]))

    def run_module(self, module, *args):
        isolation = ["-I"] if sys.flags.isolated else []
        return subprocess.run(
            [sys.executable, *isolation, "-m", "mojum." + module, *map(str, args)],
            cwd=self.root, env=self.env, capture_output=True, timeout=20,
        )

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def inventory(self):
        return {path.name: (path.read_bytes(), path.stat().st_mode,
                            path.stat().st_mtime_ns)
                for path in self.root.iterdir() if path.is_file()}

    def quiescent(self, path):
        # Byte/mtime assertions concern closed standalone files. SQLite's
        # live-WAL coordination semantics are not a filesystem snapshot claim.
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=DELETE").fetchone(),
                             ("delete",))

    def reject_unchanged(self, path, *args, diagnostic=None):
        before = self.inventory()
        result = self.run_module("aquarium", "--db", path, *args)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)
        if diagnostic is not None:
            self.assertIn(diagnostic.encode("ascii"), result.stderr)
        self.assertEqual(self.inventory(), before)

    def profile(self, label, width, sensory, neural="disabled", brain="placeholder"):
        room_file = self.root / (label + ".json")
        room_file.write_text(json.dumps({
            "schema": "mojum.world.point-room-draft/1", "width": width, "height": 4,
            "obstacles": [[2, 1, 0.1, 2]], "start": [1, 2],
        }), encoding="utf-8")
        temporal = "range_guided" if sensory == "toy_probe" else "none"
        flags = ("--room-file", room_file, "--motor-mode", "developmental_fixture",
                 "--sensory-mode", sensory, "--neural-mode", neural,
                 "--temporal-mode", temporal, "--brain-mode", brain)
        room, start = load_cli_room(room_file, "developmental_fixture")
        options = dict(room=room, start=start, motor_mode="developmental_fixture",
                       sensory_mode=sensory, neural_mode=neural, brain_mode=brain,
                       temporal_mode=temporal,
                       sensory_scene=ToyScene(width=room.width, height=room.height, toys=()))
        return flags, options

    def test_host_journal_continues_through_cli_and_back(self):
        profiles = ((4, "toy_probe", "disabled", "placeholder"),
                    (5, "toy_probe", "shadow", "developmental_controller"),
                    (4, "toy_probe_v2", "shadow", "placeholder"),
                    (5, "toy_probe_v2", "disabled", "developmental_controller"))
        for index, profile in enumerate(profiles):
            with self.subTest(profile=profile):
                flags, options = self.profile(f"room-{index}", *profile)
                db = self.root / f"host-{index}.sqlite3"
                born = self.success(self.run_module(
                    "portable_host", "--db", db, *flags, "--ticks", 2, "--interval-ms", 20))
                self.assertEqual((born["end_tick"], born["journal_events"]), (2, 3))
                self.quiescent(db)
                # This already-supported API is the replay/continuation oracle.
                reference = self.root / f"reference-{index}.sqlite3"
                shutil.copyfile(db, reference)
                with Aquarium(reference, SEED, GRAPH, **options) as aquarium:
                    before = aquarium.checkpoint()
                    aquarium.step()
                    aquarium.step()
                    expected = aquarium.checkpoint()
                replay_flags = (*flags, "--sensory-scene", "empty")
                inspected = self.success(self.run_module(
                    "aquarium", "--db", db, *replay_flags, "--ticks", 0,
                    "--expected-head", born["journal_head"]))
                self.assertEqual({key: inspected[key] for key in before}, before)
                self.assertEqual(inspected["stored_events"], 3)
                continued = self.success(self.run_module(
                    "aquarium", "--db", db, *replay_flags, "--ticks", 2,
                    "--expected-head", born["journal_head"]))
                self.assertEqual({key: continued[key] for key in expected}, expected)
                self.assertEqual(continued["stored_events"], 5)
                resumed = self.success(self.run_module(
                    "portable_host", "--db", db, *flags, "--ticks", 1, "--interval-ms", 20,
                    "--expected-head", continued["journal_head"]))
                self.assertEqual((resumed["start_tick"], resumed["end_tick"],
                                  resumed["journal_events"]), (4, 5, 6))
                trace = self.root / f"trace-{index}.jsonl"
                exported = self.success(self.run_module(
                    "aquarium_trace_export", "--db", db, *replay_flags, "--output", trace,
                    "--expected-head", resumed["journal_head"], "--allow-private-export"))
                self.assertEqual((exported["frames"], exported["journal_head"]),
                                 (5, resumed["journal_head"]))
                backup = self.root / f"backup-{index}.sqlite3"
                copied = self.success(self.run_module(
                    "aquarium_cold_copy", "export", "--db", db, *replay_flags, "--output", backup))
                verified = self.success(self.run_module(
                    "aquarium_cold_copy", "verify", "--db", backup, *replay_flags,
                    "--expected-head", resumed["journal_head"], "--expected-tick", 5,
                    "--expected-sha256", copied["db_sha256"]))
                self.assertEqual(verified["journal_head"], resumed["journal_head"])

    def test_new_authored_empty_scene_can_resume_in_host(self):
        for width, sensory in ((4, "toy_probe"), (5, "toy_probe_v2")):
            with self.subTest(width=width, sensory=sensory):
                flags, options = self.profile(f"birth-{width}", width, sensory)
                db = self.root / f"birth-{width}.sqlite3"
                born = self.success(self.run_module(
                    "aquarium", "--db", db, *flags, "--sensory-scene", "empty", "--ticks", 2))
                self.assertEqual((born["tick"], born["stored_events"]), (2, 3))
                with Aquarium(db, SEED, GRAPH, read_only=True, **options) as aquarium:
                    self.assertEqual(aquarium.checkpoint()["journal_head"], born["journal_head"])
                    self.assertEqual(aquarium.sensory_bridge.scene.toys, ())
                resumed = self.success(self.run_module(
                    "portable_host", "--db", db, *flags, "--ticks", 1, "--interval-ms", 20,
                    "--expected-head", born["journal_head"]))
                self.assertEqual((resumed["start_tick"], resumed["end_tick"],
                                  resumed["journal_events"]), (2, 3, 4))

    def test_default_selector_keeps_legacy_journal_hashes(self):
        profiles = (("scripted", "none"), ("developmental_fixture", "toy_probe"),
                    ("developmental_fixture", "toy_probe_v2"), ("local_cell_v4", "none"),
                    ("local_cell_v4", "toy_probe_v2"))
        for index, (motor, sensory) in enumerate(profiles):
            with self.subTest(motor=motor, sensory=sensory):
                flags = ("--motor-mode", motor, "--sensory-mode", sensory,
                         "--neural-mode", "disabled", "--ticks", 2)
                legacy = self.root / f"legacy-{index}.sqlite3"
                explicit = self.root / f"default-{index}.sqlite3"
                for _ in range(2):
                    omitted = self.success(self.run_module("aquarium", "--db", legacy, *flags))
                    selected = self.success(self.run_module(
                        "aquarium", "--db", explicit, *flags, "--sensory-scene", "default"))
                    self.assertEqual(selected, omitted)

    def test_invalid_selectors_do_not_adopt_or_mutate_sources(self):
        for kind in ("missing", "empty", "foreign", "valid"):
            with self.subTest(kind=kind):
                db = self.root / (kind + ".sqlite3")
                if kind == "empty":
                    db.touch()
                elif kind == "foreign":
                    with closing(sqlite3.connect(db)) as connection:
                        connection.execute("CREATE TABLE user_notes (note TEXT)")
                        connection.commit()
                elif kind == "valid":
                    with Aquarium(db, SEED, GRAPH, neural_mode="disabled") as aquarium:
                        aquarium.step()
                    self.quiescent(db)
                self.reject_unchanged(db, "--ticks", 0, "--sensory-scene", "empty",
                                      diagnostic="empty requires toy_probe or toy_probe_v2")
                self.reject_unchanged(db, "--ticks", 1, "--sensory-scene", "infer",
                                      diagnostic="invalid choice")

    def test_wrong_scene_and_anchor_preserve_existing_history(self):
        for width in (4, 5):
            flags, options = self.profile(f"refusal-{width}", width, "toy_probe_v2")
            db = self.root / f"refusal-{width}.sqlite3"
            with Aquarium(db, SEED, GRAPH, **options) as aquarium:
                aquarium.step()
            self.quiescent(db)
            for ticks in (0, 1):
                with self.subTest(width=width, ticks=ticks):
                    for selector in ((), ("--sensory-scene", "default")):
                        self.reject_unchanged(db, *flags, *selector, "--ticks", ticks)
                    self.reject_unchanged(db, *flags, "--sensory-scene", "empty", "--ticks", ticks,
                                          "--expected-head", "0" * 64)
        default = self.root / "actual-default.sqlite3"
        with Aquarium(default, SEED, GRAPH, neural_mode="disabled",
                      motor_mode="developmental_fixture", sensory_mode="toy_probe") as aquarium:
            aquarium.step()
        self.quiescent(default)
        self.reject_unchanged(default, "--neural-mode", "disabled",
                              "--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                              "--sensory-scene", "empty", "--ticks", 1, diagnostic="mode mismatch")

    def test_empty_builtin_v4_scene_keeps_its_canonical_profile(self):
        db = self.root / "v4-empty.sqlite3"
        reference = self.root / "v4-reference.sqlite3"
        with Aquarium(reference, SEED, GRAPH, neural_mode="disabled", motor_mode="local_cell_v4",
                      sensory_mode="toy_probe_v2", sensory_scene=ToyScene(toys=())) as aquarium:
            aquarium.step()
            aquarium.step()
            expected = aquarium.checkpoint()
        result = self.success(self.run_module(
            "aquarium", "--db", db, "--motor-mode", "local_cell_v4", "--sensory-mode", "toy_probe_v2",
            "--sensory-scene", "empty", "--neural-mode", "disabled", "--ticks", 2))
        self.assertEqual({key: result[key] for key in expected}, expected)
        self.assertEqual(result["stored_events"], 3)
        self.quiescent(db)
        flags, _ = self.profile("unsupported-v4-room", 4, "toy_probe_v2")
        self.reject_unchanged(db, *flags, "--motor-mode", "local_cell_v4", "--sensory-scene", "empty",
                              "--ticks", 0, diagnostic="incompatible with fixed-geometry local-cell modes")


if __name__ == "__main__":
    unittest.main()
