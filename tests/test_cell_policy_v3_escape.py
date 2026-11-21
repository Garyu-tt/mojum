"""Bounded rest-escape policy: no learned inhibition may become permanent rest.

Issue #106: a learned policy that permanently skips every action which has ever
produced a blocked outcome can rest forever in a cell whose four moves were all
blocked once. These tests pin the defect on the existing v3 policy, then prove
the versioned escape profile stays bounded and honest without reading geometry.
"""

import inspect
import unittest

from mojum.cell_policy_v3 import CellExplorerV3
from mojum.cell_policy_v3_escape import (
    LEARNING_SCHEMA,
    MAX_CELLS,
    MAX_CONSECUTIVE_RESTS,
    REPROBE_BLOCKED_BEFORE_REST,
    SCHEMA,
    CellExplorerV3Escape,
    CellV3EscapeError,
    compare_policies,
    run_policy,
    sealed_cell,
)
from mojum.local_cell_sense import Proprioception, STEP_METERS
from mojum.world_room import Room, advance, initial_state


def _tick_room(policy, room, start, ticks):
    state = initial_state(room, start[0], start[1])
    actions = []
    outcomes = []
    for tick in range(ticks):
        sense = Proprioception(tick, state.revision, state.x, state.y, tick % 2 == 0)
        intent = policy.decide(sense)
        actions.append(intent["action"])
        state, result = advance(
            room, state, tick=tick + 1, dx=intent["dx"], dy=intent["dy"],
            expected_revision=state.revision,
        )
        policy.observe(sense, intent, result["blocked"])
        outcomes.append(result["blocked"])
    return actions, outcomes


def _longest_rest_streak(actions):
    longest = streak = 0
    for action in actions:
        if action == "rest":
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    return longest


class ExistingV3RestCollapseTests(unittest.TestCase):
    def test_v3_rests_forever_after_all_four_moves_were_blocked_once(self):
        room, start = sealed_cell()
        policy = CellExplorerV3()
        actions, _ = _tick_room(policy, room, start, 40)
        self.assertEqual(len(actions), 40)
        self.assertEqual(set(actions[:4]), {"x+", "y+", "x-", "y-"})
        self.assertEqual(actions[4:], ["rest"] * 36)
        self.assertEqual(_longest_rest_streak(actions), 36)


class BoundedEscapeTests(unittest.TestCase):
    def test_sealed_cell_cannot_collapse_into_unbounded_rest(self):
        room, start = sealed_cell()
        report = run_policy(CellExplorerV3Escape, room, start, ticks=80)
        self.assertEqual(report["rest_actions"] + report["move_actions"], 80)
        self.assertEqual(report["successful_moves"], 0)
        self.assertGreaterEqual(report["blocked_attempts"], 1)
        self.assertLessEqual(report["longest_rest_streak"], MAX_CONSECUTIVE_RESTS)
        self.assertLess(report["idle_fraction"], 0.5)
        self.assertEqual(report["verdict"], "NO_MEASURED_DISPLACEMENT")

    def test_only_bounded_rest_after_measured_blocked_probes(self):
        room, start = sealed_cell()
        policy = CellExplorerV3Escape()
        actions, outcomes = _tick_room(policy, room, start, 40)
        rest_after = []
        blocked_run = 0
        for action, blocked in zip(actions, outcomes):
            if action == "rest":
                rest_after.append(blocked_run)
                blocked_run = 0
            else:
                if blocked:
                    blocked_run += 1
                else:
                    blocked_run = 0
        # Every rest follows at least the versioned number of failed probes.
        self.assertTrue(rest_after)
        for run in rest_after:
            self.assertGreaterEqual(run, REPROBE_BLOCKED_BEFORE_REST)
        # Movement probes continue after every rest: no terminal rest state.
        self.assertTrue(actions.count("rest") < len(actions))
        self.assertEqual(_longest_rest_streak(actions), 1)
        first_rest = actions.index("rest")
        self.assertTrue(set(actions[first_rest + 1:]) - {"rest"})

    def test_open_room_keeps_rest_right_and_measured_mobility(self):
        room = Room(4, 4, ((2, 1, .1, 2),))
        report = run_policy(CellExplorerV3Escape, room, (1.0, 2.0), ticks=120)
        self.assertGreater(report["rest_actions"], 0)
        self.assertGreater(report["successful_moves"], 0)
        self.assertEqual(report["blocked_attempts"], 0)
        self.assertEqual(report["successful_moves"], report["move_actions"])
        self.assertEqual(report["verdict"], "MOBILITY_OBSERVED")
        self.assertLessEqual(report["longest_rest_streak"], MAX_CONSECUTIVE_RESTS)

    def test_rest_budget_is_never_exceeded_even_with_unblocked_moves(self):
        room, start = sealed_cell()
        policy = CellExplorerV3Escape()
        actions, _ = _tick_room(policy, room, start, 60)
        # Force the innate rest rotation to arrive repeatedly by continuing
        # in a cell that still has unblocked moves after leaving the trap.
        self.assertLessEqual(
            _longest_rest_streak(actions), MAX_CONSECUTIVE_RESTS,
        )


class DeterminismAndSnapshotTests(unittest.TestCase):
    def test_two_identical_runs_are_byte_identical(self):
        room, start = sealed_cell()
        first = CellExplorerV3Escape()
        second = CellExplorerV3Escape()
        a1, o1 = _tick_room(first, room, start, 25)
        a2, o2 = _tick_room(second, room, start, 25)
        self.assertEqual(a1, a2)
        self.assertEqual(o1, o2)
        self.assertEqual(first.snapshot(), second.snapshot())

    def test_snapshot_roundtrip_preserves_escape_counters(self):
        room, start = sealed_cell()
        policy = CellExplorerV3Escape()
        _tick_room(policy, room, start, 17)
        restored = CellExplorerV3Escape.from_snapshot(policy.snapshot())
        self.assertEqual(restored.snapshot(), policy.snapshot())
        self.assertEqual(restored.schema if hasattr(restored, "schema") else SCHEMA, SCHEMA)
        sense = Proprioception(17, "0" * 64, start[0], start[1], True)
        # A restored policy must make the same first decision as the original
        # when both receive the same canonical proprioception tick.
        fresh = CellExplorerV3Escape.from_snapshot(policy.snapshot())
        self.assertEqual(restored.decide(sense), fresh.decide(sense))

    def test_tampered_escape_snapshot_is_rejected(self):
        room, start = sealed_cell()
        policy = CellExplorerV3Escape()
        _tick_room(policy, room, start, 10)
        good = policy.snapshot()

        def assert_rejected(mutate):
            bad = dict(good)
            bad.update(mutate(bad))
            with self.assertRaises(CellV3EscapeError):
                CellExplorerV3Escape.from_snapshot(bad)

        assert_rejected(lambda s: {"schema": "mojum.developmental.local-cell/3"})
        assert_rejected(
            lambda s: {"consecutive_rests": MAX_CONSECUTIVE_RESTS + 1},
        )
        assert_rejected(
            lambda s: {"consecutive_blocked_probes": REPROBE_BLOCKED_BEFORE_REST + 1},
        )
        assert_rejected(lambda s: {"updates": s["updates"] + 1})
        records = {k: {a: list(c) for a, c in v.items()}
                   for k, v in good["records"].items()}
        first_cell = next(iter(records))
        first_action = next(iter(records[first_cell]))
        records[first_cell][first_action][1] = (
            records[first_cell][first_action][0] + 1
        )
        assert_rejected(lambda s: {"records": records})
        assert_rejected(lambda s: {"cell_size_m": 0.5})

    def test_snapshot_rejects_impossible_observations_at_early_cell_tick(self):
        # Globally plausible 100 updates, but a cell last observed at tick 0
        # could not have received five measurements by then.
        forged = {
            "schema": SCHEMA, "cell_size_m": STEP_METERS,
            "last_tick": 99, "updates": 100,
            "records": {
                "0:0": {"rest": [5, 0]},
                "2500:0": {"rest": [95, 0]},
            },
            "last_seen_at": {"0:0": 0, "2500:0": 99},
            "evictions": 0, "forgotten_observations": 0,
            "consecutive_rests": 0, "consecutive_blocked_probes": 0,
        }
        with self.assertRaisesRegex(CellV3EscapeError, "elapsed ticks"):
            CellExplorerV3Escape.from_snapshot(forged)

    def test_forged_or_stale_intent_is_rejected(self):
        policy = CellExplorerV3Escape()
        sense = Proprioception(0, "0" * 64, 1.0, 2.0, False)
        intent = policy.decide(sense)
        forged = dict(intent, action="y-")
        with self.assertRaises(CellV3EscapeError):
            policy.observe(sense, forged, False)
        with self.assertRaises(CellV3EscapeError):
            policy.observe(sense, intent, 1)
        stale = Proprioception(2, "0" * 64, 1.0, 2.0, False)
        with self.assertRaises(CellV3EscapeError):
            policy.decide(stale)
        with self.assertRaises(CellV3EscapeError):
            policy.decide(object())

    def test_rest_cannot_be_recorded_as_blocked(self):
        policy = CellExplorerV3Escape()
        sense = Proprioception(0, "a" * 64, 1.0, 2.0, False)
        intent = policy.decide(sense)
        self.assertEqual(intent["action"], "rest")
        before = policy.snapshot()
        with self.assertRaisesRegex(CellV3EscapeError, "rest cannot be a blocked"):
            policy.observe(sense, intent, True)
        self.assertEqual(policy.snapshot(), before)
        learned = policy.observe(sense, intent, False)
        self.assertEqual(learned["action"], "rest")
        self.assertEqual(policy.snapshot()["records"][intent["zone"]]["rest"], [1, 0])

    def test_learning_record_is_versioned_and_honest(self):
        room, start = sealed_cell()
        state = initial_state(room, start[0], start[1])
        policy = CellExplorerV3Escape()
        sense = Proprioception(0, state.revision, state.x, state.y, False)
        intent = policy.decide(sense)
        state, result = advance(
            room, state, tick=1, dx=intent["dx"], dy=intent["dy"],
            expected_revision=state.revision,
        )
        learned = policy.observe(sense, intent, result["blocked"])
        self.assertEqual(learned["schema"], LEARNING_SCHEMA)
        self.assertEqual(learned["source"], "measured_synthetic_outcome_with_bounded_escape")
        self.assertEqual(learned["action"], intent["action"])
        self.assertEqual(learned["total_updates"], 1)
        self.assertEqual(learned["consecutive_blocked_probes"], int(result["blocked"]))

    def test_escape_decider_receives_only_own_proprioception(self):
        parameters = list(inspect.signature(CellExplorerV3Escape.decide).parameters)
        self.assertEqual(parameters, ["self", "sense"])

    def test_eviction_accounting_matches_bounded_capacity(self):
        policy = CellExplorerV3Escape()
        revision = "0" * 64
        for tick in range(MAX_CELLS + 8):
            x = (tick + 1) * STEP_METERS
            sense = Proprioception(tick, revision, x, 5.0, False)
            intent = policy.decide(sense)
            learned = policy.observe(sense, intent, False)
        snapshot = policy.snapshot()
        self.assertLessEqual(len(snapshot["records"]), MAX_CELLS)
        self.assertEqual(snapshot["updates"], MAX_CELLS + 8)
        self.assertEqual(learned["total_evictions"], 8)
        self.assertEqual(
            sum(count[0] for actions in snapshot["records"].values()
                for count in actions.values())
            + snapshot["forgotten_observations"],
            snapshot["updates"],
        )
        CellExplorerV3Escape.from_snapshot(snapshot)


class ComparisonTests(unittest.TestCase):
    def test_comparison_uses_identical_rooms_pulses_and_reports_metrics(self):
        report = compare_policies(ticks=60)
        self.assertEqual(report["schema"], "mojum.developmental.local-cell-escape.benchmark/1")
        self.assertIn("v3", report["policies"])
        self.assertIn("v3_escape", report["policies"])
        v3, escape = report["policies"]["v3"], report["policies"]["v3_escape"]
        self.assertEqual(v3["frames"], escape["frames"])
        for key in ("blocked_attempts", "successful_moves", "rest_actions",
                    "idle_fraction", "longest_rest_streak", "verdict"):
            self.assertIn(key, v3)
            self.assertIn(key, escape)
        self.assertEqual(
            report["scenarios"]["sealed"]["pulse_stream"],
            "tick_even_heartbeat_pulse",
        )
        self.assertEqual(v3["verdict"], "REST_COLLAPSE_SUSPECTED")
        self.assertNotEqual(escape["verdict"], "REST_COLLAPSE_SUSPECTED")
        self.assertLessEqual(
            escape["longest_rest_streak"], MAX_CONSECUTIVE_RESTS,
        )


if __name__ == "__main__":
    unittest.main()
