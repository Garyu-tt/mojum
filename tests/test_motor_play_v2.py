"""Regression tests for the isolated bounded-rest motor-play v2 profile (issue #106)."""

import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mojum.motor_play_v2 import (
    ACTION_NAMES,
    EXPLORATION_QUOTA,
    MAX_CONSECUTIVE_REST,
    MOVES,
    PROFILE,
    SCHEMA,
    MotorPlayV2,
    MotorPlayV2Error,
)
from mojum.motor_play_v2_eval import (
    ROOMS,
    SCHEMA as EVAL_SCHEMA,
    EvalError,
    evaluate_named,
    main,
    run_profile,
)
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent


def _drive(ticks, room=None, start=None):
    default_room, default_start = ROOMS["canonical"]
    return run_profile(default_room if room is None else room,
                       default_start if start is None else start, ticks)


class DecisionLadderTests(unittest.TestCase):
    def test_initial_probes_are_each_cardinal_direction(self):
        policy = MotorPlayV2()
        kinds = []
        for tick in range(len(MOVES)):
            intent = policy.choose(tick)
            kinds.append(intent["kind"])
            self.assertEqual(intent["reason"], "initial_cardinal_probe")
            self.assertEqual(intent["prior_trials"], 0)
            policy.observe(tick=tick, kind=intent["kind"], blocked=False)
        self.assertEqual(tuple(kinds), tuple(name for name, _, _ in MOVES))

    def test_exploration_quota_precedes_scoring(self):
        policy = MotorPlayV2()
        for tick in range(len(MOVES)):
            policy.observe(tick=tick, kind=policy.choose(tick)["kind"], blocked=False)
        # One sample of each direction is still below the quota: keep probing
        # the least-tried direction rather than trusting a measured score.
        intent = policy.choose(len(MOVES))
        self.assertEqual(intent["reason"], "exploration_quota")
        self.assertEqual(policy.samples[intent["kind"]]["trials"], 1)

    def test_no_external_avoid_or_prefer_input_can_ban_directions(self):
        params = list(inspect.signature(MotorPlayV2.choose).parameters)
        self.assertEqual(params, ["self", "tick"])
        self.assertFalse(hasattr(MotorPlayV2, "avoid"))

    def test_rest_cap_forces_a_move_and_blocks_further_rest(self):
        raw = {
            "schema": SCHEMA, "profile": PROFILE,
            "observations": MAX_CONSECUTIVE_REST,
            "rest_streak": MAX_CONSECUTIVE_REST,
            "max_consecutive_rest": MAX_CONSECUTIVE_REST,
            "pending": None,
            "samples": {name: {"trials": 0, "blocked": 0} for name in ACTION_NAMES},
        }
        raw["samples"]["rest"] = {"trials": MAX_CONSECUTIVE_REST, "blocked": 0}
        policy = MotorPlayV2.from_snapshot(raw)
        intent = policy.choose(MAX_CONSECUTIVE_REST)
        self.assertNotEqual(intent["kind"], "rest")
        self.assertEqual(intent["reason"], "rest_cap_forced_move")
        self.assertEqual(intent["rest_streak"], MAX_CONSECUTIVE_REST)
        with self.assertRaises(MotorPlayV2Error):
            policy.observe(tick=MAX_CONSECUTIVE_REST, kind="rest", blocked=False)
        outcome = policy.observe(tick=MAX_CONSECUTIVE_REST, kind=intent["kind"], blocked=False)
        self.assertEqual(outcome["rest_streak"], 0)

    def test_stale_or_invalid_decision_rejected(self):
        policy = MotorPlayV2()
        for bad in (-1, 1, True, 0.0, "0"):
            with self.assertRaises(MotorPlayV2Error):
                policy.choose(bad)
        with self.assertRaises(MotorPlayV2Error):
            policy.observe(tick=0, kind="north", blocked=False)
        policy.choose(0)
        with self.assertRaises(MotorPlayV2Error):
            policy.observe(tick=0, kind="rest", blocked=1)


class SnapshotContractTests(unittest.TestCase):
    def _valid_raw(self):
        policy = MotorPlayV2()
        for tick in range(6):
            intent = policy.choose(tick)
            policy.observe(tick=tick, kind=intent["kind"], blocked=tick % 2 == 0)
        return policy.snapshot()

    def test_round_trip_preserves_decisions(self):
        raw = self._valid_raw()
        restored = MotorPlayV2.from_snapshot(raw)
        self.assertEqual(restored.snapshot(), raw)
        self.assertEqual(restored.choose(6), MotorPlayV2.from_snapshot(raw).choose(6))

    def test_snapshot_is_json_serialisable_and_versioned(self):
        raw = self._valid_raw()
        self.assertEqual(json.loads(json.dumps(raw)), raw)
        self.assertEqual(raw["schema"], SCHEMA)
        self.assertEqual(raw["profile"], PROFILE)

    def test_malformed_snapshots_rejected(self):
        base = self._valid_raw()
        mutations = []
        for key in ("schema", "profile", "observations", "rest_streak",
                    "max_consecutive_rest", "samples"):
            broken = json.loads(json.dumps(base))
            broken[key] = None
            mutations.append(broken)
        extra = json.loads(json.dumps(base))
        extra["unexpected"] = 1
        mutations.append(extra)
        bool_obs = json.loads(json.dumps(base))
        bool_obs["observations"] = True
        mutations.append(bool_obs)
        mismatch = json.loads(json.dumps(base))
        mismatch["samples"]["east"]["trials"] += 1
        mutations.append(mismatch)
        too_many_rests = json.loads(json.dumps(base))
        too_many_rests["rest_streak"] = MAX_CONSECUTIVE_REST + 1
        mutations.append(too_many_rests)
        over_cap = json.loads(json.dumps(base))
        over_cap["rest_streak"] = MAX_CONSECUTIVE_REST
        over_cap["samples"]["rest"] = {"trials": 1, "blocked": 0}
        mutations.append(over_cap)
        pending_rest = json.loads(json.dumps(base))
        pending_rest["rest_streak"] = MAX_CONSECUTIVE_REST
        pending_rest["pending"] = "rest"
        pending_rest["samples"]["rest"] = {"trials": 1, "blocked": 0}
        mutations.append(pending_rest)
        unknown_pending = json.loads(json.dumps(base))
        unknown_pending["pending"] = "northeast"
        mutations.append(unknown_pending)
        for index, broken in enumerate(mutations):
            with self.subTest(index=index):
                with self.assertRaises(MotorPlayV2Error):
                    MotorPlayV2.from_snapshot(broken)


class EvaluationHarnessTests(unittest.TestCase):
    def test_zero_ticks_is_empty_and_well_formed(self):
        report = _drive(0)
        self.assertEqual(report["ticks"], 0)
        self.assertEqual(report["blocked_total"], 0)
        self.assertEqual(report["idle_fraction"], 0.0)
        self.assertEqual(report["longest_rest_streak"], 0)
        self.assertEqual(report["schema"], EVAL_SCHEMA)
        self.assertEqual(report["interface"], "evaluation_only_not_aquarium_abi")

    def test_canonical_room_moves_without_rest_collapse(self):
        report = _drive(240)
        self.assertGreater(report["successful_moves"], 0)
        self.assertGreater(report["distinct_positions"], 1)
        self.assertGreater(report["distinct_zones_50cm"], 1)
        self.assertLessEqual(report["longest_rest_streak"], MAX_CONSECUTIVE_REST)
        self.assertLess(report["idle_fraction"], 0.25)

    def test_every_direction_blocked_still_keeps_attempting(self):
        # Worst case for learned inhibition: no cardinal step can ever succeed.
        report = _drive(240, room=ROOMS["blocked_all"][0], start=(0.1, 0.1))
        self.assertGreaterEqual(report["attempts"]["east"], EXPLORATION_QUOTA)
        for name, _, _ in MOVES:
            self.assertGreaterEqual(report["attempts"][name], EXPLORATION_QUOTA)
        self.assertLessEqual(report["longest_rest_streak"], MAX_CONSECUTIVE_REST)
        self.assertLess(report["idle_fraction"], 0.25)
        self.assertGreater(report["blocked_total"], 0)

    def test_held_out_fixtures_all_run_with_bounded_streak(self):
        for name in sorted(ROOMS):
            with self.subTest(fixture=name):
                report = evaluate_named(name, 120)
                self.assertLessEqual(report["longest_rest_streak"], MAX_CONSECUTIVE_REST)
                self.assertEqual(sum(report["attempts"].values()), 120)

    def test_runs_are_deterministic(self):
        first, second = _drive(120), _drive(120)
        self.assertEqual(first, second)

    def test_invalid_requests_rejected(self):
        room, start = ROOMS["canonical"]
        with self.assertRaises(EvalError):
            run_profile("not a room", start, 1)
        for ticks in (-1, 1.0, True, 10_001):
            with self.assertRaises(EvalError):
                run_profile(room, start, ticks)
        with self.assertRaises(EvalError):
            evaluate_named("missing_fixture", 1)

    def test_cli_emits_json_for_requested_fixtures(self):
        self.assertEqual(main(["--fixture", "canonical", "--ticks", "13"]), 0)


class IsolationTests(unittest.TestCase):
    def test_canonical_aquarium_does_not_import_v2_profile(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        script = (
            "import sys, mojum.aquarium;"
            "assert 'mojum.motor_play_v2' not in sys.modules;"
            "assert 'mojum.motor_play_v2_eval' not in sys.modules;"
            "print('isolated')"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "isolated")

    def test_v1_schema_is_untouched(self):
        from mojum.developmental import SCHEMA as V1_SCHEMA
        self.assertEqual(V1_SCHEMA, "mojum.developmental.motor-play/1")
        self.assertNotEqual(V1_SCHEMA, SCHEMA)


if __name__ == "__main__":
    unittest.main()
