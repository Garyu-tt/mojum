"""A failed graph tick must never leave a partial in-memory episode."""
import copy
import json
from pathlib import Path
import unittest

from mojum.graph import GraphError
from mojum.sim import Simulation

GRAPH = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class SimulationAtomicStepTests(unittest.TestCase):
    @staticmethod
    def with_late_room(light):
        graph = copy.deepcopy(GRAPH)
        graph["nodes"].append({
            "id": "zzz_bad_room",
            "kind": "world.room",
            "config": {"light": light},
            "notes": {"purpose": "Independent node after the episode sink"},
        })
        return graph

    def test_late_executor_failure_rolls_back_episode_and_tick(self):
        sim = Simulation(self.with_late_room("invalid"))
        # This is a legal typed DAG: an independent node can run *after*
        # the memory sink, so validation at graph construction succeeds.
        self.assertLess(sim.order.index("episode"),
                        sim.order.index("zzz_bad_room"))
        initial = sim.snapshot()
        for _ in range(2):
            with self.assertRaisesRegex(GraphError, "Invalid world.room light"):
                sim.step()
            self.assertEqual(sim.snapshot(), initial)
            self.assertEqual(sim.tick, 0)
            self.assertEqual(sim.episodes, [])

    def test_complete_tick_commits_episode_once(self):
        sim = Simulation(self.with_late_room("dim"))
        result = sim.step()
        self.assertEqual(sim.tick, 1)
        self.assertEqual(len(sim.episodes), 1)
        self.assertEqual(sim.episodes[0], result["episode"]["episode"])
        self.assertEqual(result["zzz_bad_room"]["world"]["light"], "dim")
        self.assertEqual(len(Simulation(self.with_late_room("dim")).episodes), 0)


if __name__ == "__main__":
    unittest.main()
