"""Standalone simulation checkpoint acceptance: checksum alone is not replay."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mojum.graph import GraphError
from mojum.sim import Simulation, digest

GRAPH = json.loads((Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8"))


def _graph_without_episode_sink():
    graph = copy.deepcopy(GRAPH)
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "episode"]
    graph["edges"] = [edge for edge in graph["edges"] if edge["to"]["node"] != "episode"]
    return graph


class SimulationRestoreReplayTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation(GRAPH)
        for _ in range(5):
            self.sim.step()
        self.checkpoint = self.sim.snapshot()

    def _forge(self, mutation):
        forged = copy.deepcopy(self.checkpoint)
        mutation(forged["state"])
        forged["sha256"] = digest(forged["state"])
        return forged

    def _assert_rejected_atomically(self, snapshot, pattern):
        live_before = self.sim.snapshot()
        with self.assertRaisesRegex(GraphError, pattern):
            self.sim.restore(snapshot)
        self.assertEqual(self.sim.snapshot(), live_before)

    def test_valid_restoration_replays_and_continues_identically(self):
        expected = [self.sim.step() for _ in range(9)]
        self.sim.restore(self.checkpoint)
        self.assertEqual([self.sim.step() for _ in range(9)], expected)

    def test_checksum_rewritten_brain_actuation_is_not_accepted(self):
        forged = self._forge(lambda state: state["episodes"][2]["brain_observation"].update({"action": {"dx": 10}}))
        self._assert_rejected_atomically(forged, "deterministic replay")

    def test_checksum_rewritten_sensory_observation_is_not_accepted(self):
        forged = self._forge(lambda state: state["episodes"][3]["sensory"]["heartbeat"].update({"phase_milli": 999}))
        self._assert_rejected_atomically(forged, "deterministic replay")

    def test_checksum_rewritten_provenance_is_not_accepted(self):
        forged = self._forge(lambda state: state["episodes"][1].update({"provenance": "external"}))
        self._assert_rejected_atomically(forged, "deterministic replay")

    def test_boolean_timestamp_for_tick_zero_is_not_accepted(self):
        forged = self._forge(lambda state: state["episodes"][0].update({"observed_at_ns": False}))
        self._assert_rejected_atomically(forged, "episode sequence")

    def test_extra_snapshot_field_rejected_even_with_rewritten_checksum(self):
        forged = self._forge(lambda state: state.update({"phantom": True}))
        self._assert_rejected_atomically(forged, "state shape")

    def test_malformed_json_rejected_as_graph_error_atomically(self):
        forged = copy.deepcopy(self.checkpoint)
        forged["state"]["episodes"][0]["sensory"]["heartbeat"]["pulse"] = {1, 2}
        self._assert_rejected_atomically(forged, "noncanonical JSON")

    def test_empty_history_restores(self):
        fresh = Simulation(GRAPH)
        self.sim.restore(fresh.snapshot())
        self.assertEqual(self.sim.tick, 0)
        self.assertEqual(self.sim.episodes, [])

    def test_graph_without_episode_sink_restores_its_own_clock(self):
        graph = _graph_without_episode_sink()
        original = Simulation(graph)
        for _ in range(7):
            original.step()
        snapshot = original.snapshot()
        self.assertEqual(snapshot["state"]["episodes"], [])

        restored = Simulation(graph)
        with patch.object(Simulation, "step", autospec=True, side_effect=Simulation.step) as step:
            restored.restore(snapshot)
        step.assert_called_once()
        self.assertEqual(restored.snapshot(), snapshot)
        expected = [original.step() for _ in range(10)]
        self.assertEqual({frame["heart"]["pulse"]["pulse"] for frame in expected}, {False, True})
        self.assertEqual([restored.step() for _ in range(10)], expected)
        self.assertEqual(restored.snapshot(), original.snapshot())

        # A checksum-rewritten phantom episode still cannot be restored.
        malformed = copy.deepcopy(snapshot)
        malformed["state"]["episodes"].append({"tick": 0})
        malformed["sha256"] = digest(malformed["state"])
        unchanged = restored.snapshot()
        with self.assertRaisesRegex(GraphError, "Inconsistent snapshot state"):
            restored.restore(malformed)
        self.assertEqual(restored.snapshot(), unchanged)

    def test_graph_without_episode_sink_rejects_ticks_it_cannot_execute(self):
        graph = _graph_without_episode_sink()
        room = next(node for node in graph["nodes"] if node["id"] == "room")
        room["config"]["light"] = "invalid"
        restored = Simulation(graph)
        empty = restored.snapshot()
        restored.restore(empty)

        forged = copy.deepcopy(empty)
        forged["state"]["tick"] = 1
        forged["sha256"] = digest(forged["state"])
        with self.assertRaisesRegex(GraphError, "Invalid world.room light fixture"):
            restored.restore(forged)
        self.assertEqual(restored.snapshot(), empty)

    def test_two_episode_sinks_restore_and_replay_each_record(self):
        graph = copy.deepcopy(GRAPH)
        second_sink = copy.deepcopy(next(n for n in graph["nodes"] if n["id"] == "episode"))
        second_sink["id"] = "episode_copy"
        graph["nodes"].append(second_sink)
        for edge in list(graph["edges"]):
            if edge["to"]["node"] == "episode":
                duplicated_edge = copy.deepcopy(edge)
                duplicated_edge["to"]["node"] = "episode_copy"
                graph["edges"].append(duplicated_edge)

        original = Simulation(graph)
        for _ in range(4):
            original.step()
        snapshot = original.snapshot()
        self.assertEqual(len(snapshot["state"]["episodes"]), 8)
        self.assertEqual(
            [e["tick"] for e in snapshot["state"]["episodes"]],
            [0, 0, 1, 1, 2, 2, 3, 3],
        )

        restored = Simulation(graph)
        restored.restore(snapshot)
        self.assertEqual(restored.snapshot(), snapshot)
        self.assertEqual(restored.step(), original.step())
        self.assertEqual(restored.snapshot(), original.snapshot())

        # The second copy at tick zero must be checked, not just one
        # arbitrarily chosen memory sink per tick.
        forged = copy.deepcopy(snapshot)
        forged["state"]["episodes"][1]["brain_observation"]["action"] = {"dx": 1}
        forged["sha256"] = digest(forged["state"])
        unchanged = restored.snapshot()
        with self.assertRaisesRegex(GraphError, "deterministic replay"):
            restored.restore(forged)
        self.assertEqual(restored.snapshot(), unchanged)

        # Both sinks' tick metadata are checked against the same clock.
        forged = copy.deepcopy(snapshot)
        forged["state"]["episodes"][3]["tick"] = 99
        forged["sha256"] = digest(forged["state"])
        with self.assertRaisesRegex(GraphError, "episode sequence"):
            restored.restore(forged)
        self.assertEqual(restored.snapshot(), unchanged)

if __name__ == "__main__":
    unittest.main()
