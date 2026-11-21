"""Regression tests for issue #106: no permanent rest collapse in zoned motor play."""

import copy
import json
import unittest

from mojum.developmental_escape import (
    FORCED_EXPLORATION_PERIOD, MAX_CONSECUTIVE_RESTS, MAX_OBSERVATIONS,
    MAX_ZONES, MOVE_NAMES, SCHEMA, EscapePolicyError, ZonedEscapePlay,
)

POSITION = (0.1, 0.1)


def simulate(policy, ticks, *, position=POSITION, blocked=True):
    """Drive the policy against a measured point-room outcome for each action."""
    kinds = []
    for _ in range(ticks):
        intent = policy.choose(policy.observations, position)
        kinds.append(intent["kind"])
        blocked_outcome = blocked if intent["kind"] in MOVE_NAMES else False
        policy.observe(
            tick=policy.observations,
            position=position,
            kind=intent["kind"],
            blocked=blocked_outcome,
        )
    return kinds


def longest_run(values, target):
    best = run = 0
    for value in values:
        run = run + 1 if value == target else 0
        best = max(best, run)
    return best


class ZonedEscapeLocalityTests(unittest.TestCase):
    def test_blocked_direction_is_not_suppressed_in_a_distant_zone(self):
        policy = ZonedEscapePlay()
        for tick in range(9):
            policy.observe(tick=tick, position=POSITION, kind="east", blocked=True)
        self.assertEqual(policy.choose(9, POSITION)["kind"], "north")
        far = policy.choose(9, (5.1, 5.1))
        self.assertEqual(far["kind"], "east")
        self.assertEqual(far["zone"], "10:10")
        self.assertEqual(far["expected_unblocked_probability"], 0.5)

    def test_negative_coordinates_use_floor_zone_keys(self):
        intent = ZonedEscapePlay().choose(0, (-0.1, -0.1))
        self.assertEqual(intent["zone"], "-1:-1")

    def test_hints_are_bounded_and_validated(self):
        policy = ZonedEscapePlay()
        for hints in (("oracle",), ("east", "east"), ("east",) * 5, "east", (1,)):
            with self.assertRaises(EscapePolicyError):
                policy.choose(0, POSITION, avoid=hints)
        with self.assertRaises(EscapePolicyError):
            policy.choose(0, POSITION, prefer=("rest",))
        self.assertEqual(policy.observations, 0)


class ZonedEscapeAntiCollapseTests(unittest.TestCase):
    def test_total_blockage_never_collapses_into_permanent_rest(self):
        policy = ZonedEscapePlay()
        kinds = simulate(policy, 200)
        for name in MOVE_NAMES:
            self.assertIn(name, kinds)
        self.assertLessEqual(longest_run(kinds, "rest"), MAX_CONSECUTIVE_RESTS)
        self.assertLess(kinds.count("rest") / len(kinds), 0.5)
        self.assertEqual(max(kinds.count(name) for name in MOVE_NAMES) > 0, True)

    def test_permanent_avoid_hint_cannot_starve_suppressed_actions(self):
        policy = ZonedEscapePlay()
        kinds, forced = [], []
        for _ in range(64):
            intent = policy.choose(
                policy.observations, POSITION, avoid=("east", "north", "west"),
            )
            kinds.append(intent["kind"])
            forced.append(intent["forced"])
            policy.observe(
                tick=policy.observations, position=POSITION,
                kind=intent["kind"], blocked=False,
            )
        # Past the deterministic initial sample of the four directions, a
        # permanent avoid hint would otherwise leave only ``south`` eligible.
        suppressed = set(kinds[4:])
        self.assertTrue({"east", "north", "west"} <= suppressed)
        self.assertIn("exploration", forced)

    def test_rest_streak_cap_forces_a_move_at_a_rest_cadence_tick(self):
        state = ZonedEscapePlay().snapshot()
        state["observations"] = 21
        state["rest"] = {"trials": 21, "blocked": 0}
        state["rest_streak"] = MAX_CONSECUTIVE_RESTS
        state["last_attempt"] = {name: 20 for name in state["last_attempt"]}
        policy = ZonedEscapePlay.from_snapshot(state)
        intent = policy.choose(21, POSITION)
        self.assertIn(intent["kind"], MOVE_NAMES)
        self.assertEqual(intent["forced"], "rest_cap")

    def test_every_action_is_force_probed_at_the_bounded_period(self):
        policy = ZonedEscapePlay()
        forced = []
        for _ in range(4 + 2 * FORCED_EXPLORATION_PERIOD):
            intent = policy.choose(policy.observations, POSITION)
            forced.append(intent["forced"])
            policy.observe(
                tick=policy.observations, position=POSITION,
                kind=intent["kind"], blocked=False,
            )
        self.assertIn("exploration", forced)
        self.assertLessEqual(
            max(forced.index("exploration"), 4), 4 + FORCED_EXPLORATION_PERIOD,
        )


class ZonedEscapeStateTests(unittest.TestCase):
    def test_snapshot_round_trip_and_deterministic_replay(self):
        first, second = ZonedEscapePlay(), ZonedEscapePlay()
        left = simulate(first, 40)
        right = simulate(second, 40)
        self.assertEqual(left, right)
        self.assertEqual(first.snapshot(), second.snapshot())
        restored = ZonedEscapePlay.from_snapshot(first.snapshot())
        self.assertEqual(restored.snapshot(), first.snapshot())
        self.assertEqual(restored.choose(40, POSITION), first.choose(40, POSITION))

    def test_malformed_state_fails_closed_without_mutating_input(self):
        base = ZonedEscapePlay()
        simulate(base, 12)
        good = base.snapshot()
        mutations = []
        for key, value in (
            ("schema", "mojum.other/1"),
            ("observations", -1),
            ("observations", True),
            ("rest_streak", -1),
            ("evicted_trials", -1),
        ):
            bad = copy.deepcopy(good)
            bad[key] = value
            mutations.append(bad)
        bad = copy.deepcopy(good)
        bad["rest"]["blocked"] = 99
        mutations.append(bad)
        bad = copy.deepcopy(good)
        bad["last_attempt"] = {"east": 0}
        mutations.append(bad)
        bad = copy.deepcopy(good)
        bad["zones"]["nope"] = {"last_touch": 0, "rows": {}}
        mutations.append(bad)
        bad = copy.deepcopy(good)
        bad["extra"] = 1
        mutations.append(bad)
        for bad in mutations:
            before = copy.deepcopy(bad)
            with self.assertRaises(EscapePolicyError):
                ZonedEscapePlay.from_snapshot(bad)
            self.assertEqual(bad, before)

    def test_zone_table_is_bounded_and_evictions_are_accounted(self):
        policy = ZonedEscapePlay()
        for tick in range(MAX_ZONES + 2):
            position = (float(tick), 0.0)
            policy.observe(tick=tick, position=position, kind="east", blocked=False)
        state = policy.snapshot()
        self.assertEqual(len(state["zones"]), MAX_ZONES)
        self.assertEqual(state["evicted_trials"], 2)
        restored = ZonedEscapePlay.from_snapshot(state)
        self.assertEqual(restored.snapshot(), state)

    def test_observation_budget_is_enforced_on_both_sides(self):
        state = ZonedEscapePlay().snapshot()
        state["observations"] = MAX_OBSERVATIONS
        state["rest"] = {"trials": MAX_OBSERVATIONS, "blocked": 0}
        full = ZonedEscapePlay.from_snapshot(state)
        with self.assertRaises(EscapePolicyError):
            full.choose(MAX_OBSERVATIONS, POSITION)
        with self.assertRaises(EscapePolicyError):
            full.observe(tick=MAX_OBSERVATIONS, position=POSITION,
                         kind="east", blocked=False)

    def test_outcomes_are_validated_and_rest_cannot_be_blocked(self):
        policy = ZonedEscapePlay()
        for kwargs in (
            {"tick": 1, "position": POSITION, "kind": "east", "blocked": False},
            {"tick": 0, "position": POSITION, "kind": "oracle", "blocked": False},
            {"tick": 0, "position": POSITION, "kind": "east", "blocked": 1},
            {"tick": 0, "position": POSITION, "kind": "rest", "blocked": True},
            {"tick": 0, "position": (float("nan"), 0.0), "kind": "east", "blocked": False},
        ):
            with self.assertRaises(EscapePolicyError):
                policy.observe(**kwargs)
        self.assertEqual(policy.observations, 0)

    def test_boundary_exposes_no_room_target_or_future_observation(self):
        policy = ZonedEscapePlay()
        intent = policy.choose(0, POSITION)
        self.assertEqual(set(intent), {
            "schema", "kind", "dx", "dy", "zone",
            "expected_unblocked_probability", "prior_trials",
            "rest_streak", "forced", "interface",
        })
        self.assertEqual(intent["schema"], SCHEMA)
        self.assertLessEqual(abs(intent["dx"]) + abs(intent["dy"]), 0.125)
        state = policy.snapshot()
        for forbidden in ("room", "target", "goal", "reward", "future"):
            self.assertNotIn(forbidden, state)
        self.assertNotIn("obstacle", json.dumps(state))


if __name__ == "__main__":
    unittest.main()
