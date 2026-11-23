import copy
import unittest

from mojum.cell_policy_v3_escape import CellExplorerV3Escape, CellV3EscapeError
from mojum.local_cell_sense import Proprioception, STEP_METERS


class CausalTailTests(unittest.TestCase):
    def test_tail_is_bounded_and_round_trips_after_eviction(self):
        policy = CellExplorerV3Escape()
        for tick in range(520):
            sense = Proprioception(tick, "b" * 64, (tick + 1) * STEP_METERS, 5.0, False)
            intent = policy.decide(sense)
            policy.observe(sense, intent, False)
        snapshot = policy.snapshot()
        self.assertEqual(len(snapshot["outcome_tail"]), 4)
        self.assertEqual(CellExplorerV3Escape.from_snapshot(snapshot).snapshot(), snapshot)
        self.assertEqual(len(snapshot["records"]), 512)

    def test_interleaved_rest_move_rest_cannot_claim_two_terminal_rests(self):
        snapshot = CellExplorerV3Escape().snapshot()
        snapshot.update({
            "last_tick": 2, "updates": 3,
            "records": {"0:0": {"rest": [2, 0], "x+": [1, 0]}},
            "last_seen_at": {"0:0": 2}, "evictions": 0, "forgotten_observations": 0,
            "consecutive_rests": 2, "consecutive_blocked_probes": 0,
            "outcome_tail": [
                {"tick": 0, "cell": "0:0", "action": "rest", "blocked": False},
                {"tick": 1, "cell": "0:0", "action": "x+", "blocked": False},
                {"tick": 2, "cell": "0:0", "action": "rest", "blocked": False},
            ],
        })
        with self.assertRaisesRegex(CellV3EscapeError, "terminal streak counters"):
            CellExplorerV3Escape.from_snapshot(snapshot)

    def test_interleaved_blocked_success_blocked_cannot_claim_two_probes(self):
        snapshot = CellExplorerV3Escape().snapshot()
        snapshot.update({
            "last_tick": 2, "updates": 3,
            "records": {"0:0": {"x+": [2, 2], "y+": [1, 0]}},
            "last_seen_at": {"0:0": 2}, "evictions": 0, "forgotten_observations": 0,
            "consecutive_rests": 0, "consecutive_blocked_probes": 2,
            "outcome_tail": [
                {"tick": 0, "cell": "0:0", "action": "x+", "blocked": True},
                {"tick": 1, "cell": "0:0", "action": "y+", "blocked": False},
                {"tick": 2, "cell": "0:0", "action": "x+", "blocked": True},
            ],
        })
        with self.assertRaisesRegex(CellV3EscapeError, "terminal streak counters"):
            CellExplorerV3Escape.from_snapshot(snapshot)

    def test_round_trip_at_operational_streak_ceilings(self):
        # Rest is capped at three consecutive ticks; blocked re-probes are
        # capped at the four-entry chronology window. Keep fixtures within
        # those reachable limits instead of constructing impossible states.
        rest = CellExplorerV3Escape().snapshot()
        rest.update({
            "last_tick": 2,
            "updates": 3,
            "records": {"0:0": {"rest": [3, 0]}},
            "last_seen_at": {"0:0": 2},
            "consecutive_rests": 3,
            "outcome_tail": [
                {"tick": tick, "cell": "0:0", "action": "rest", "blocked": False}
                for tick in range(3)
            ],
            "history_complete": False,
        })
        self.assertEqual(CellExplorerV3Escape.from_snapshot(rest).snapshot(), rest)

        blocked = CellExplorerV3Escape().snapshot()
        blocked.update({
            "last_tick": 3,
            "updates": 4,
            "records": {"0:0": {"x+": [4, 4]}},
            "last_seen_at": {"0:0": 3},
            "consecutive_blocked_probes": 4,
            "outcome_tail": [
                {"tick": tick, "cell": "0:0", "action": "x+", "blocked": True}
                for tick in range(4)
            ],
            "history_complete": True,
        })
        self.assertEqual(CellExplorerV3Escape.from_snapshot(blocked).snapshot(), blocked)


    def test_hostile_mapping_keys_and_schema_values_are_rejected_before_comparison(self):
        class HostileKey(str):
            armed = False
            def __hash__(self):
                if self.armed:
                    raise AssertionError("hostile key hash executed")
                return str.__hash__(self)
            def __eq__(self, other):
                if self.armed:
                    raise AssertionError("hostile key equality executed")
                return str.__eq__(self, other)

        class HostileText(str):
            def __eq__(self, other):
                raise AssertionError("hostile schema equality executed")

        snapshot = CellExplorerV3Escape().snapshot()
        root_key = HostileKey("schema")
        snapshot[root_key] = snapshot.pop("schema")
        root_key.armed = True
        with self.assertRaisesRegex(CellV3EscapeError, "mapping keys"):
            CellExplorerV3Escape.from_snapshot(snapshot)

        snapshot = CellExplorerV3Escape().snapshot()
        snapshot["schema"] = HostileText(snapshot["schema"])
        with self.assertRaisesRegex(CellV3EscapeError, "retention envelope"):
            CellExplorerV3Escape.from_snapshot(snapshot)

        snapshot = CellExplorerV3Escape().snapshot()
        tail_key = HostileKey("tick")
        snapshot["outcome_tail"] = [{
            tail_key: 0, "cell": "0:0", "action": "rest", "blocked": False,
        }]
        tail_key.armed = True
        snapshot["last_tick"] = 0
        snapshot["updates"] = 1
        snapshot["records"] = {"0:0": {"rest": [1, 0]}}
        snapshot["last_seen_at"] = {"0:0": 0}
        snapshot["history_complete"] = False
        snapshot["consecutive_rests"] = 1
        with self.assertRaisesRegex(CellV3EscapeError, "mapping keys"):
            CellExplorerV3Escape.from_snapshot(snapshot)

    def test_legacy_zero_streak_snapshot_is_upgraded(self):
        legacy = CellExplorerV3Escape().snapshot()
        legacy.pop("snapshot_version")
        legacy.pop("history_complete")
        legacy.pop("outcome_tail")
        restored = CellExplorerV3Escape.from_snapshot(legacy)
        self.assertEqual(restored.snapshot()["snapshot_version"], 2)
        self.assertEqual(restored.snapshot()["outcome_tail"], [])

    def test_legacy_nonzero_streak_fails_closed(self):
        legacy = CellExplorerV3Escape().snapshot()
        legacy.pop("snapshot_version")
        legacy.pop("history_complete")
        legacy.pop("outcome_tail")
        legacy.update({
            "last_tick": 0, "updates": 1, "records": {"0:0": {"rest": [1, 0]}},
            "last_seen_at": {"0:0": 0}, "consecutive_rests": 1,
        })
        with self.assertRaisesRegex(CellV3EscapeError, "legacy snapshot lacks chronology"):
            CellExplorerV3Escape.from_snapshot(legacy)

    def test_tail_must_be_contiguous_and_aggregate_backed(self):
        policy = CellExplorerV3Escape()
        for tick in range(6):
            sense = Proprioception(tick, "c" * 64, (tick + 1) * STEP_METERS, 5.0, False)
            intent = policy.decide(sense)
            policy.observe(sense, intent, False)
        snapshot = policy.snapshot()
        broken = copy.deepcopy(snapshot)
        broken["outcome_tail"][-1]["tick"] -= 1
        with self.assertRaisesRegex(CellV3EscapeError, "chronology tail"):
            CellExplorerV3Escape.from_snapshot(broken)
        broken = copy.deepcopy(snapshot)
        broken["outcome_tail"].pop(0)
        with self.assertRaisesRegex(CellV3EscapeError, "completeness flag"):
            CellExplorerV3Escape.from_snapshot(broken)
        broken = copy.deepcopy(snapshot)
        broken["outcome_tail"][-1]["action"] = "not-an-action"
        with self.assertRaisesRegex(CellV3EscapeError, "invalid measured-outcome chronology tail"):
            CellExplorerV3Escape.from_snapshot(broken)


if __name__ == "__main__":
    unittest.main()
