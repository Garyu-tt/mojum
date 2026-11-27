"""An Aquarium must admit its executable graph before touching its journal."""

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.genesis import ACQUISITION_SCHEMA
from mojum.graph import validate


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class AquariumGraphExecutionAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def graph_with_light(self, light):
        graph = copy.deepcopy(GRAPH)
        next(node for node in graph["nodes"]
             if node["kind"] == "world.room")["config"]["light"] = light
        # The failure belongs to execution, not graph topology/JSON admission.
        validate(graph)
        return graph

    def prepare(self, kind, directory):
        directory.mkdir()
        path = directory / "journal.sqlite3"
        if kind == "empty":
            path.touch()
        elif kind == "unrelated":
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute("CREATE TABLE user_notes (note TEXT)")
                    connection.execute("INSERT INTO user_notes VALUES ('keep this')")
        elif kind == "valid":
            with Aquarium(path, SEED, GRAPH) as aquarium:
                aquarium.step()
        return path

    def files(self, directory):
        return {path.name: (path.read_bytes(), path.stat().st_mode,
                            path.stat().st_mtime_ns)
                for path in directory.iterdir()}

    def test_api_rejects_unexecutable_graph_before_any_journal_change(self):
        graph = self.graph_with_light("not-a-light")
        for kind in ("absent", "empty", "unrelated", "valid"):
            with self.subTest(kind=kind):
                directory = self.root / kind
                path = self.prepare(kind, directory)
                before = self.files(directory)
                with self.assertRaisesRegex(AquariumError, "graph cannot execute"):
                    with Aquarium(path, SEED, graph):
                        pass
                self.assertEqual(self.files(directory), before)

    def test_other_json_light_values_cannot_initialize_a_journal(self):
        for index, light in enumerate((None, True, 1, [], {})):
            with self.subTest(light=light):
                path = self.root / f"invalid-{index}.sqlite3"
                with self.assertRaisesRegex(AquariumError, "graph cannot execute"):
                    with Aquarium(path, SEED, self.graph_with_light(light)):
                        pass
                self.assertFalse(path.exists())

    def test_cli_zero_and_positive_ticks_fail_cleanly_without_writes(self):
        graph_file = self.root / "invalid-graph.json"
        graph_file.write_text(json.dumps(self.graph_with_light("not-a-light")),
                              encoding="utf-8")
        for kind in ("absent", "empty", "unrelated", "valid"):
            for ticks in (0, 1):
                with self.subTest(kind=kind, ticks=ticks):
                    directory = self.root / f"{kind}-{ticks}"
                    path = self.prepare(kind, directory)
                    before = self.files(directory)
                    result = subprocess.run(
                        [sys.executable, "-m", "mojum.aquarium", "--db", str(path),
                         "--graph", str(graph_file), "--ticks", str(ticks)],
                        capture_output=True, text=True, encoding="utf-8", timeout=10,
                        env={**os.environ, "PYTHONPATH": str(ROOT / "src"),
                             "PYTHONDONTWRITEBYTECODE": "1"},
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("graph cannot execute", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(self.files(directory), before)

    def test_valid_lights_and_virtual_intervals_start_at_the_first_real_tick(self):
        for light in ("dark", "dim", "bright", "default"):
            graph = self.graph_with_light("dim" if light == "default" else light)
            if light == "default":
                next(node for node in graph["nodes"]
                     if node["kind"] == "world.room")["config"].pop("light")
            for dt_ns in (1_000, 10_000_000, 1_000_000_000):
                with self.subTest(light=light, dt_ns=dt_ns):
                    path = self.root / f"{light}-{dt_ns}.sqlite3"
                    with Aquarium(path, SEED, graph, dt_ns=dt_ns) as aquarium:
                        self.assertEqual(aquarium.tick, 0)
                        self.assertEqual(aquarium.world.tick, 0)
                        self.assertEqual(aquarium.stored_events, 1)
                        frame = aquarium.step()
                        self.assertEqual(frame["tick"], 0)
                        self.assertEqual(frame["time_ns"], 0)
                        self.assertEqual(frame["brain"]["world_light"],
                                         "dim" if light == "default" else light)
                        expected = aquarium.checkpoint()
                    with Aquarium(path, SEED, graph, dt_ns=dt_ns) as reopened:
                        self.assertEqual(reopened.checkpoint(), expected)
                        self.assertEqual(reopened.step()["time_ns"], dt_ns)

    def test_genesis_binding_keeps_raw_bpm_override_and_future_acquisitions(self):
        graph = copy.deepcopy(GRAPH)
        next(node for node in graph["nodes"]
             if node["kind"] == "genesis.seed")["config"]["bpm"] = "overridden"
        original = copy.deepcopy(graph)
        path = self.root / "bound.sqlite3"
        with Aquarium(path, SEED, graph) as aquarium:
            self.assertEqual(aquarium.stored_events, 1)
            self.assertEqual(aquarium.tick, 0)
            self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 60)
            aquarium.acquire({
                "schema": ACQUISITION_SCHEMA, "event_id": "future-bpm",
                "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm", "delta": 30,
                "valid_from_tick": 2, "observed_at_ns": 0, "recorded_at_ns": 10_000_000,
                "source": "synthetic-execution-test", "evidence": "fixture only",
            })
            self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 60)
            self.assertEqual(aquarium.step()["phenotype"]["parameters"]["heartbeat_bpm"], 90)
            expected = aquarium.checkpoint()
        self.assertEqual(graph, original)
        with Aquarium(path, SEED, graph) as reopened:
            self.assertEqual(reopened.checkpoint(), expected)
            self.assertEqual(reopened.step()["phenotype"]["parameters"]["heartbeat_bpm"], 90)

    def test_published_mode_journal_heads_and_cold_replay_remain_unchanged(self):
        # Real three-frame journals captured on accepted main 61c48dcf before
        # this admission change. Disabled neural mode avoids interpreter-specific
        # shadow summation while attesting the complete bootstrap/frame wire.
        cases = (
            ({}, "a43a5a6ce85d96c8e613970c634d29a9beb4c2381baa4836dd765ac3137d8400"),
            ({"motor_mode": "developmental_fixture"},
             "1f64c34b4815a454b5dcc8648658798e86d3e197e137a6715025d816881cf062"),
            ({"motor_mode": "developmental_fixture", "brain_mode": "developmental_controller",
              "sensory_mode": "toy_probe", "temporal_mode": "range_guided"},
             "adf44d54fd99fed031ee7e71fa2cbe5f9e0bcdb9013d2f4ecfa97f0bf0688fe9"),
            ({"motor_mode": "local_cell_v2b"},
             "da2918887d8ce9256ea386a1b2f3f10bd32119e6b120b5004a642e957ff32e33"),
            ({"motor_mode": "local_cell_v3"},
             "fe8489f2d730ce5b926fdb04b1eaf70f8a3d513284ae6a58c9e117852d935dd4"),
            ({"motor_mode": "local_cell_v3_escape"},
             "70b022e7a4ec16d879b86d98544f2a62ea8201e8a57b4b2986847bc5e6aabeb6"),
            ({"motor_mode": "local_cell_v4", "sensory_mode": "toy_probe_v2"},
             "15d88a550e104640253ed20be64f32d2e739d918fa407f197397775df3597ae7"),
        )
        for index, (options, expected_head) in enumerate(cases):
            with self.subTest(options=options):
                path = self.root / f"profile-{index}.sqlite3"
                with Aquarium(path, SEED, GRAPH, neural_mode="disabled", **options) as aquarium:
                    for _ in range(3):
                        aquarium.step()
                    expected = aquarium.checkpoint()
                    self.assertEqual(expected["journal_head"], expected_head)
                    self.assertEqual(aquarium.stored_events, 4)
                with Aquarium(path, SEED, GRAPH, neural_mode="disabled", **options) as reopened:
                    self.assertEqual(reopened.checkpoint(), expected)
                    self.assertEqual(reopened.step()["tick"], 3)
                    continued = reopened.checkpoint()
                with Aquarium(path, SEED, GRAPH, neural_mode="disabled", read_only=True,
                              **options) as readonly:
                    self.assertEqual(readonly.checkpoint(), continued)


if __name__ == "__main__":
    unittest.main()
