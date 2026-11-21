"""Standalone simulation checkpoint acceptance: checksum alone is not replay."""
import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError
from mojum.sim import Simulation, digest

GRAPH = json.loads((Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8"))


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


if __name__ == "__main__":
    unittest.main()
