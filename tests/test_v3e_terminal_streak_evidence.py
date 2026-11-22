"""Reject terminal v3-escape streaks without enough retained measured outcomes.

Source QA follow-up to #441 after the older draft diverged from current main.
No SQLite, network, real physical actuation or model provider.
"""
import unittest

from mojum.cell_policy_v3_escape import (
    SCHEMA, STEP_METERS, CellExplorerV3Escape, CellV3EscapeError,
    Proprioception,
)


class TerminalStreakEvidenceTests(unittest.TestCase):
    def test_forged_rest_streak_with_only_one_measured_rest(self):
        # All ordinary snapshot envelopes are valid: three observations in
        # one cell, latest tick 2, and a real rest observation exists.
        # Only the causal claim of THREE consecutive rests is fabricated.
        state = {
            "schema": SCHEMA, "cell_size_m": STEP_METERS,
            "last_tick": 2, "updates": 3,
            "records": {"0:0": {"rest": [1, 0], "x+": [2, 0]}},
            "last_seen_at": {"0:0": 2}, "evictions": 0,
            "forgotten_observations": 0, "consecutive_rests": 3,
            "consecutive_blocked_probes": 0,
        }
        with self.assertRaisesRegex(CellV3EscapeError,
                                    "rest streak lacks measured outcomes"):
            CellExplorerV3Escape.from_snapshot(state)

    def test_forgotten_old_outcome_cannot_forge_recent_probe_streak(self):
        # Eviction at tick 512 is otherwise plausible: 513 observations,
        # one forgotten and 512 retained cells. Only the *latest* retained
        # cell contains a blocked probe. A forgotten ancient observation
        # cannot justify a claimed two-tick terminal blocked-probe streak.
        records = {
            f"{tick}:0": {"x+": [1, int(tick == 512)]}
            for tick in range(1, 513)
        }
        state = {
            "schema": SCHEMA, "cell_size_m": STEP_METERS,
            "last_tick": 512, "updates": 513, "records": records,
            "last_seen_at": {f"{tick}:0": tick for tick in range(1, 513)},
            "evictions": 1, "forgotten_observations": 1,
            "consecutive_rests": 0, "consecutive_blocked_probes": 2,
        }
        with self.assertRaisesRegex(CellV3EscapeError,
                                    "blocked-probe streak lacks measured outcomes"):
            CellExplorerV3Escape.from_snapshot(state)

    def test_recent_movement_cell_invalidates_rest_streak(self):
        # Tick 1 was necessarily a movement, hence ticks 1..2 cannot be
        # consecutive rests even though two older aggregate rests exist.
        state = {
            "schema": SCHEMA, "cell_size_m": STEP_METERS,
            "last_tick": 2, "updates": 3,
            "records": {
                "0:0": {"rest": [2, 0]},
                "1:0": {"x+": [1, 0]},
            },
            "last_seen_at": {"0:0": 2, "1:0": 1},
            "evictions": 0, "forgotten_observations": 0,
            "consecutive_rests": 2, "consecutive_blocked_probes": 0,
        }
        with self.assertRaisesRegex(CellV3EscapeError,
                                    "recent cell lacks measured rest"):
            CellExplorerV3Escape.from_snapshot(state)

    def test_recent_unblocked_cell_invalidates_probe_streak(self):
        # Tick 1 was necessarily unblocked; two blocked outcomes in an
        # older cell cannot justify a two-tick terminal blocked streak.
        state = {
            "schema": SCHEMA, "cell_size_m": STEP_METERS,
            "last_tick": 2, "updates": 3,
            "records": {
                "0:0": {"x+": [2, 2]},
                "1:0": {"y+": [1, 0]},
            },
            "last_seen_at": {"0:0": 2, "1:0": 1},
            "evictions": 0, "forgotten_observations": 0,
            "consecutive_rests": 0, "consecutive_blocked_probes": 2,
        }
        with self.assertRaisesRegex(CellV3EscapeError,
                                    "recent cell lacks measured blocked move"):
            CellExplorerV3Escape.from_snapshot(state)

    def test_valid_cross_cell_blocked_probe_streak_roundtrips(self):
        policy = CellExplorerV3Escape()
        for tick in range(2):
            sense = Proprioception(
                tick, "0" * 64, (tick + 1) * STEP_METERS, 2.0, True
            )
            intent = policy.decide(sense)
            self.assertNotEqual(intent["action"], "rest")
            policy.observe(sense, intent, blocked=True)
        state = policy.snapshot()
        self.assertEqual(state["consecutive_blocked_probes"], 2)
        self.assertEqual(len(state["records"]), 2)
        self.assertEqual(CellExplorerV3Escape.from_snapshot(state).snapshot(),
                         state)

    def test_valid_cross_cell_rest_streak_roundtrips(self):
        policy = CellExplorerV3Escape()
        actions = []
        for tick in range(6):
            sense = Proprioception(
                tick, "0" * 64, 1.0 if tick < 5 else 2.0, 2.0, tick == 4
            )
            intent = policy.decide(sense)
            actions.append(intent["action"])
            policy.observe(sense, intent, blocked=False)
        self.assertEqual(actions[-2:], ["rest", "rest"])
        state = policy.snapshot()
        self.assertEqual(state["consecutive_rests"], 2)
        self.assertEqual(CellExplorerV3Escape.from_snapshot(state).snapshot(),
                         state)


if __name__ == "__main__":
    unittest.main()
