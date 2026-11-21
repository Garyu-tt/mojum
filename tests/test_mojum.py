import copy
import json
import unittest
from pathlib import Path
from mojum.graph import GraphError, validate, PORTS
from mojum.sim import Simulation


GRAPH = json.loads((Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class GraphContractTests(unittest.TestCase):
    def test_fixture_topology(self):
        nodes, _, order = validate(GRAPH)
        self.assertEqual(len(nodes), 6)
        self.assertLess(order.index("genome"), order.index("heart"))
        self.assertLess(order.index("sense"), order.index("episode"))

    def test_duplicate_ids_fail(self):
        g = copy.deepcopy(GRAPH)
        g["nodes"][1]["id"] = "genome"
        with self.assertRaises(GraphError): validate(g)

    def test_unregistered_executable_node_fails(self):
        g = copy.deepcopy(GRAPH)
        g["nodes"][0]["kind"] = "python.exec"
        with self.assertRaises(GraphError): validate(g)

    def test_missing_notes_fail(self):
        g = copy.deepcopy(GRAPH)
        g["nodes"][0]["notes"] = {}
        with self.assertRaises(GraphError): validate(g)

    def test_undeclared_port_fail(self):
        g = copy.deepcopy(GRAPH)
        g["edges"][0]["to"]["port"] = "unknown"
        with self.assertRaises(GraphError): validate(g)

    def test_bad_type_fail(self):
        g = copy.deepcopy(GRAPH)
        g["edges"][0]["from"] = {"node": "room", "port": "world"}
        with self.assertRaises(GraphError): validate(g)

    def test_cycle_fail(self):
        # Test cycle detection with a temporary same-type test-only registry entry.
        PORTS["test.loop"] = ({"in": "loop/1"}, {"out": "loop/1"})
        try:
            g = {"schema": GRAPH["schema"], "id": GRAPH["id"],
                 "revision": GRAPH["revision"], "nodes": [
                {"id": name, "kind": "test.loop", "config": {}, "notes": {"purpose": "test"}}
                for name in ("a", "b")], "edges": [
                {"from": {"node": "a", "port": "out"}, "to": {"node": "b", "port": "in"}},
                {"from": {"node": "b", "port": "out"}, "to": {"node": "a", "port": "in"}}
            ]}
            with self.assertRaisesRegex(GraphError, "Cycle detected"): validate(g)
        finally:
            del PORTS["test.loop"]


class RunnerTests(unittest.TestCase):
    def test_cpu_fixture_runs(self):
        sim = Simulation(GRAPH)
        for _ in range(120): sim.step()
        self.assertEqual(sim.tick, 120)
        self.assertEqual(len(sim.episodes), 120)
        self.assertEqual(sim.episodes[-1]["brain_observation"]["status"], "UNIMPLEMENTED")

    def test_heartbeat_provides_change_without_external_stimulus(self):
        sim = Simulation(GRAPH)
        statuses = []
        for _ in range(120):
            frame = sim.step()
            statuses.append(frame["heart"]["pulse"]["pulse"])
        self.assertIn(True, statuses)
        self.assertIn(False, statuses)

    def test_replay_exact(self):
        sim = Simulation(GRAPH)
        for _ in range(43): sim.step()
        s = sim.snapshot()
        first = [sim.step() for _ in range(25)]
        sim.restore(s)
        second = [sim.step() for _ in range(25)]
        self.assertEqual(first, second)

    def test_tampered_snapshot_rejected(self):
        sim = Simulation(GRAPH)
        sim.step()
        s = sim.snapshot()
        s["state"]["episodes"][0]["tick"] = 999
        with self.assertRaises(GraphError): sim.restore(s)

    def test_self_consistent_but_different_graph_revision_rejected(self):
        sim = Simulation(GRAPH)
        sim.step()
        checkpoint = sim.snapshot()
        other = copy.deepcopy(GRAPH)
        other["revision"] = "0.2.0"
        with self.assertRaises(GraphError): Simulation(other).restore(checkpoint)

    def test_invalid_bpm_rejected(self):
        g = copy.deepcopy(GRAPH)
        g["nodes"][0]["config"]["bpm"] = 0
        with self.assertRaises(GraphError): Simulation(g).step()

    def test_static_room_is_not_an_environment(self):
        r = Simulation(GRAPH).step()
        self.assertEqual(r["room"]["world"]["kind"], "static-room-fixture")
        self.assertIsNone(r["brain"]["observation"]["action"])

    def test_episodes_do_not_mutate_previous_results(self):
        s = Simulation(GRAPH)
        result = s.step()
        result["episode"]["episode"]["tick"] = 1000
        self.assertEqual(s.episodes[0]["tick"], 0)


if __name__ == "__main__": unittest.main()
