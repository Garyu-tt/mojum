"""Cold QA for residual v3-escape snapshot chronology and LRU invariants.

Standalone, CPU-only tests. No private journals, devices, or network access.
"""
import unittest

from mojum.cell_policy_v3_escape import (
    MAX_CELLS, CellExplorerV3Escape, CellV3EscapeError,
)
from mojum.local_cell_sense import Proprioception, STEP_METERS


def _snapshot(**changes):
    state = CellExplorerV3Escape().snapshot()
    state.update(changes)
    return state


class EscapeRetentionAcceptanceTests(unittest.TestCase):
    def test_duplicate_last_seen_tick_across_cells_rejected(self):
        forged = _snapshot(
            last_tick=1, updates=2,
            records={"0:0": {"x+": [1, 0]}, "1:0": {"y+": [1, 0]}},
            last_seen_at={"0:0": 1, "1:0": 1},
        )
        with self.assertRaisesRegex(CellV3EscapeError, "multiple escape cells"):
            CellExplorerV3Escape.from_snapshot(forged)

    def test_latest_retained_observation_cannot_be_stale(self):
        forged = _snapshot(
            last_tick=99, updates=100,
            records={"0:0": {"x+": [51, 0]}, "1:0": {"y+": [49, 0]}},
            last_seen_at={"0:0": 50, "1:0": 98},
        )
        with self.assertRaisesRegex(CellV3EscapeError, "does not match tick"):
            CellExplorerV3Escape.from_snapshot(forged)

    def test_eviction_requires_full_prior_capacity(self):
        forged = _snapshot(
            last_tick=1, updates=2,
            records={"0:0": {"x+": [1, 0]}},
            last_seen_at={"0:0": 1},
            evictions=1, forgotten_observations=1,
        )
        with self.assertRaisesRegex(CellV3EscapeError, "impossible escape eviction"):
            CellExplorerV3Escape.from_snapshot(forged)

    def test_evicted_table_cannot_shrink_below_full_capacity(self):
        forged = _snapshot(
            last_tick=MAX_CELLS, updates=MAX_CELLS + 1,
            records={"0:0": {"x+": [1, 0]}},
            last_seen_at={"0:0": MAX_CELLS},
            evictions=1, forgotten_observations=MAX_CELLS,
        )
        with self.assertRaisesRegex(CellV3EscapeError, "retention shrank"):
            CellExplorerV3Escape.from_snapshot(forged)

    def test_observed_history_cannot_lose_all_retained_cells(self):
        forged = _snapshot(
            last_tick=0, updates=1, records={}, last_seen_at={},
            evictions=1, forgotten_observations=1,
        )
        with self.assertRaisesRegex(CellV3EscapeError, "without a retained cell"):
            CellExplorerV3Escape.from_snapshot(forged)

    def test_combined_deadlines_refuse_impossible_history(self):
        policy = CellExplorerV3Escape()
        for tick, cell in enumerate((2, 0, 0, 1, 1, 2)):
            sense = Proprioception(
                tick, "0" * 64, cell * STEP_METERS, 1.0, False,
            )
            policy.observe(sense, policy.decide(sense), False)
        valid = policy.snapshot()
        self.assertEqual(CellExplorerV3Escape.from_snapshot(valid).snapshot(), valid)
        valid["last_seen_at"].update({"0:25": 1, "1:25": 2})
        with self.assertRaisesRegex(CellV3EscapeError, "combined escape cell observations"):
            CellExplorerV3Escape.from_snapshot(valid)

    def test_real_capacity_eviction_restores_unchanged(self):
        policy = CellExplorerV3Escape()
        for tick in range(MAX_CELLS + 8):
            sense = Proprioception(
                tick, "0" * 64, (tick + 1) * STEP_METERS, 5.0, False,
            )
            policy.observe(sense, policy.decide(sense), False)
        state = policy.snapshot()
        self.assertEqual(len(state["records"]), MAX_CELLS)
        self.assertEqual(state["evictions"], 8)
        self.assertEqual(
            CellExplorerV3Escape.from_snapshot(state).snapshot(), state,
        )


if __name__ == "__main__":
    unittest.main()
