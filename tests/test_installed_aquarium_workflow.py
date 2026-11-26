"""Real foreground-host lifecycle and export interoperability, also from a wheel."""
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

from mojum.aquarium import Aquarium, load_cli_room
from mojum.body_genetics import CHROMOSOME_SCHEMA
from mojum.sensory_play import ToyScene


ROOT = Path(__file__).resolve().parents[1]
DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))
SOMATIC = {
    "schema": CHROMOSOME_SCHEMA, "genome_id": SEED["genome_id"],
    "inherited": {"height_bias_milli": 1000, "head_ratio_bias_milli": 1000,
                  "leg_ratio_bias_milli": 1000, "shoulder_ratio_bias_milli": 1000},
}


class InstalledAquariumWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONIOENCODING": "ascii:strict"}

    def run_module(self, name, *args):
        # A wheel's isolated interpreter keeps every subprocess isolated too.
        isolation = ["-I"] if sys.flags.isolated else []
        return subprocess.run(
            [sys.executable, *isolation, "-m", "mojum." + name, *map(str, args)],
            cwd=self.root, env=self.env, capture_output=True, timeout=30,
        )

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def inventory(self):
        return {path.name: (path.stat().st_mode, path.read_bytes())
                for path in self.root.iterdir() if path.is_file()}

    def quiescent(self, db):
        # Exact file checks use a closed standalone journal. The lifecycle
        # suite separately exercises fresh committed live-WAL pages.
        with closing(sqlite3.connect(db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=DELETE").fetchone(),
                             ("delete",))

    def host_fixture(self, *, width, sensory="toy_probe", neural="disabled",
                     brain="placeholder"):
        room_file = self.root / f"room-{width}.json"
        room_file.write_text(json.dumps({
            "schema": "mojum.world.point-room-draft/1", "width": width, "height": 4,
            "obstacles": [[2, 1, 0.1, 2]], "start": [1, 2],
        }), encoding="utf-8")
        temporal = "range_guided" if sensory == "toy_probe" else "none"
        flags = ("--room-file", room_file, "--motor-mode", "developmental_fixture",
                 "--sensory-mode", sensory, "--neural-mode", neural,
                 "--temporal-mode", temporal, "--brain-mode", brain)
        db = self.root / f"organism-{width}.sqlite3"
        born = self.success(self.run_module("portable_host", "--db", db,
                                          "--ticks", 2, "--interval-ms", 20, *flags))
        self.assertEqual((born["end_tick"], born["journal_events"]), (2, 3))
        room, start = load_cli_room(room_file, "developmental_fixture")
        options = dict(room=room, start=start, motor_mode="developmental_fixture",
                       sensory_mode=sensory, neural_mode=neural, temporal_mode=temporal,
                       brain_mode=brain, sensory_scene=ToyScene(
                           width=room.width, height=room.height, toys=()))
        self.quiescent(db)
        return db, flags, options, born

    def reject_unchanged(self, command):
        before = self.inventory()
        result = self.run_module(*command)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(self.inventory(), before)

    def test_host_development_resume_trace_and_cold_copy(self):
        profiles = ((4, "toy_probe", "disabled", "placeholder"),
                    (5, "toy_probe_v2", "shadow", "developmental_controller"))
        for width, sensory, neural, brain in profiles:
            with self.subTest(width=width, sensory=sensory, neural=neural, brain=brain):
                db, flags, options, born = self.host_fixture(
                    width=width, sensory=sensory, neural=neural, brain=brain)
                replay_flags = (*flags, "--sensory-scene", "empty")
                with Aquarium(db, SEED, GRAPH, read_only=True, **options) as aquarium:
                    before = aquarium.checkpoint()
                    height_before = aquarium.body_preview(SOMATIC)["dimensions"]["height_mm"]
                inventory = self.inventory()
                status = self.success(self.run_module("aquarium_development", "status",
                                                     "--db", db, *replay_flags))
                self.assertEqual(self.inventory(), inventory)
                self.assertEqual(status["journal_head"], born["journal_head"])
                advanced = self.success(self.run_module(
                    "aquarium_development", "advance", "--db", db, *replay_flags,
                    "--months", 6, "--expected-head", status["journal_head"],
                ))
                self.assertEqual((advanced["tick"], advanced["stored_events"],
                                  advanced["development_months"]), (2, 4, 78))
                with Aquarium(db, SEED, GRAPH, read_only=True, **options) as aquarium:
                    after = aquarium.checkpoint()
                    self.assertGreater(aquarium.body_preview(SOMATIC)["dimensions"]["height_mm"],
                                       height_before)
                self.assertEqual(after["journal_head"], advanced["journal_head"])
                changed_keys = {"journal_head", "development_months", "development_years_floor"}
                self.assertEqual({key: value for key, value in before.items() if key not in changed_keys},
                                 {key: value for key, value in after.items() if key not in changed_keys})
                self.quiescent(db)
                self.reject_unchanged((
                    "aquarium_development", "advance", "--db", db, *replay_flags,
                    "--months", 6, "--expected-head", status["journal_head"],
                ))
                resumed = self.success(self.run_module(
                    "portable_host", "--db", db, *flags, "--ticks", 2,
                    "--interval-ms", 20, "--expected-head", advanced["journal_head"],
                ))
                self.assertEqual((resumed["start_tick"], resumed["end_tick"],
                                  resumed["journal_events"]), (2, 4, 6))
                with Aquarium(db, SEED, GRAPH, read_only=True, **options) as aquarium:
                    final = aquarium.checkpoint()
                self.assertEqual(final["development_months"], 78)
                self.quiescent(db)
                source_bytes = db.read_bytes()
                trace = self.root / f"trace-{width}.jsonl"
                exported = self.success(self.run_module(
                    "aquarium_trace_export", "--db", db, *replay_flags,
                    "--output", trace, "--allow-private-export",
                    "--expected-head", resumed["journal_head"],
                ))
                self.assertEqual(exported["frames"], 4)
                self.assertEqual(exported["journal_head"], final["journal_head"])
                self.assertEqual(exported["trace_sha256"], hashlib.sha256(trace.read_bytes()).hexdigest())
                rows = [json.loads(line) for line in trace.read_text("utf-8").splitlines()]
                self.assertEqual([row["tick"] for row in rows[:-1]], [0, 1, 2, 3])
                self.assertEqual(rows[-1]["trace_room"], {"width": width, "height": 4})
                copy = self.root / f"copy-{width}.sqlite3"
                copied = self.success(self.run_module(
                    "aquarium_cold_copy", "export", "--db", db, *replay_flags, "--output", copy,
                ))
                self.assertEqual((copied["tick"], copied["event_count"]), (4, 6))
                verification_before = self.inventory()
                verified = self.success(self.run_module(
                    "aquarium_cold_copy", "verify", "--db", copy, *replay_flags,
                    "--expected-head", final["journal_head"], "--expected-tick", 4,
                    "--expected-sha256", copied["db_sha256"],
                ))
                self.assertEqual(verified["journal_head"], final["journal_head"])
                self.assertEqual(self.inventory(), verification_before)
                self.assertEqual(db.read_bytes(), source_bytes)
                with Aquarium(copy, SEED, GRAPH, read_only=True, **options) as aquarium:
                    self.assertEqual(aquarium.checkpoint(), final)

    def test_wrong_or_invalid_scene_preserves_authored_journals(self):
        for width in (4, 5):
            with self.subTest(width=width):
                db, flags, _, born = self.host_fixture(width=width)
                selectors = ((), ("--sensory-scene", "default"),
                             ("--sensory-scene", "infer"),
                             ("--sensory-scene", "empty", "--sensory-mode", "none"))
                for selector in selectors:
                    for operation in ("status", "advance"):
                        with self.subTest(selector=selector, operation=operation):
                            extra = (() if operation == "status" else
                                     ("--months", 1, "--expected-head", born["journal_head"]))
                            self.reject_unchanged(("aquarium_development", operation,
                                                   "--db", db, *flags, *selector, *extra))

    def test_explicit_default_keeps_legacy_toys_and_empty_cannot_rewrite_them(self):
        db = self.root / "legacy.sqlite3"
        flags = ("--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                 "--temporal-mode", "range_guided", "--neural-mode", "disabled")
        self.success(self.run_module("portable_host", "--db", db, *flags,
                                     "--ticks", 2, "--interval-ms", 20))
        self.quiescent(db)
        omitted = self.success(self.run_module("aquarium_development", "status", "--db", db, *flags))
        explicit = self.success(self.run_module("aquarium_development", "status", "--db", db,
                                               *flags, "--sensory-scene", "default"))
        self.assertEqual(explicit, omitted)
        for operation in ("status", "advance"):
            extra = (() if operation == "status" else
                     ("--months", 1, "--expected-head", omitted["journal_head"]))
            self.reject_unchanged(("aquarium_development", operation, "--db", db,
                                   *flags, "--sensory-scene", "empty", *extra))
        advanced = self.success(self.run_module(
            "aquarium_development", "advance", "--db", db, *flags, "--sensory-scene", "default",
            "--months", 1, "--expected-head", omitted["journal_head"],
        ))
        self.assertEqual((advanced["tick"], advanced["stored_events"],
                          advanced["development_months"]), (2, 4, 73))


if __name__ == "__main__":
    unittest.main()
