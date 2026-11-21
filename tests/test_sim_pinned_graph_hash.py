"""Regression: a checkpoint must identify the graph actually pinned for execution.

The caller can supply a mutable mapping subclass that changes its own contents
while it is being copied.  Simulation must not hash that later mutated source.
No external files, SQLite journals or real organisms are touched.
"""
import copy
import json
from pathlib import Path
import unittest

from mojum.sim import Simulation, digest


GRAPH = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class MutatingCallerGraph(dict):
    def __deepcopy__(self, memo):
        pinned = copy.deepcopy(dict(self), memo)
        # This change happens after the runtime's isolated graph was copied.
        self["revision"] = "changed-by-caller-after-copy"
        return pinned


class SimulationGraphIdentityTests(unittest.TestCase):
    def test_checkpoint_hash_uses_executed_graph_not_mutated_input(self):
        expected = copy.deepcopy(GRAPH)
        caller_graph = MutatingCallerGraph(copy.deepcopy(GRAPH))

        sim = Simulation(caller_graph)

        self.assertEqual(sim.graph, expected)
        self.assertNotEqual(caller_graph["revision"], sim.graph["revision"])
        self.assertEqual(sim.graph_hash, digest(expected))
        self.assertEqual(sim.snapshot()["state"]["graph_sha256"], digest(sim.graph))

        # A fresh runner with the original graph can cold-restore this state.
        restored = Simulation(expected)
        restored.restore(sim.snapshot())
        self.assertEqual(restored.snapshot(), sim.snapshot())


if __name__ == "__main__":
    unittest.main()
