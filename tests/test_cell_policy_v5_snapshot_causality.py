"""Regression tests for v5 anti-stall snapshot causality.

Stacked on #431 without changing the persisted /5 schema or motor decisions.
"""
import unittest

from mojum.cell_policy_v5 import (
    CellExplorerV5, CellV5Error, MAX_REST_STREAK,
)
from mojum.epoch_sense_v2 import EpochProprioception


def sense(tick):
    return EpochProprioception(
        tick, tick // 9999, tick % 9999, "a" * 64, 1.0, 1.0, False
    )


class V5SnapshotCausalityTests(unittest.TestCase):
    def test_forged_initial_rest_is_not_a_valid_policy_history(self):
        snapshot = CellExplorerV5().snapshot()
        snapshot.update({
            "last_tick": 0, "updates": 1, "rest_streak": 1,
            "records": {"25:25": {"rest": [1, 0]}},
            "last_seen_at": {"25:25": 0},
        })
        # The old validator accepted this because a counted rest existed,
        # even though no move in that cell had ever been measured as blocked.
        with self.assertRaisesRegex(CellV5Error, "rest streak without four blocked"):
            CellExplorerV5.from_snapshot(snapshot)

    def test_forced_probe_cannot_occur_on_first_tick(self):
        policy = CellExplorerV5()
        observation = sense(0)
        intent = policy.decide(observation)
        self.assertNotEqual(intent["action"], "rest")
        policy.observe(observation, intent, False)
        forged = policy.snapshot()
        forged["forced_probes"] = 1
        with self.assertRaisesRegex(CellV5Error, "impossible v5 forced-probe"):
            CellExplorerV5.from_snapshot(forged)

    def test_valid_three_rests_and_first_forced_probe_replay(self):
        policy = CellExplorerV5()
        actions = []
        for tick in range(8):
            observation = sense(tick)
            intent = policy.decide(observation)
            actions.append(intent)
            policy.observe(observation, intent, intent["action"] != "rest")
            # Every real prefix, including the three-rest prefix, must
            # remain restorable unchanged.
            snapshot = policy.snapshot()
            self.assertEqual(
                CellExplorerV5.from_snapshot(snapshot).snapshot(), snapshot
            )
        self.assertEqual([item["action"] for item in actions[4:7]],
                         ["rest"] * MAX_REST_STREAK)
        self.assertTrue(actions[7]["forced_probe"])
        self.assertEqual(policy.forced_probes, 1)
        forged = policy.snapshot()
        forged["forced_probes"] = 2
        with self.assertRaisesRegex(CellV5Error, "impossible v5 forced-probe"):
            CellExplorerV5.from_snapshot(forged)


if __name__ == "__main__":
    unittest.main()
