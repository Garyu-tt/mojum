"""Independent negative-control and replay probes for Mojum R0.6 evaluation."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from mojum.world_room import advance, initial_state

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
from evaluate_transfer_r06 import (  # noqa: E402 - example runner is a local fixture
    EVAL_SEEDS, GOAL, MODES, ROOMS, START, TRAIN_ROOM, _episode, evaluate,
)


class HeldoutTransferQATests(unittest.TestCase):
    def test_heldout_scenes_are_disjoint_and_protocol_is_deterministic(self):
        self.assertEqual(len(ROOMS), 5)
        self.assertEqual(len(set(EVAL_SEEDS)), len(EVAL_SEEDS))
        for label, (room, _classification) in ROOMS.items():
            self.assertNotEqual(room.obstacles, TRAIN_ROOM.obstacles)
            if label != "open_floor":
                self.assertNotEqual(room.obstacles, ())
        # The open-floor control is the only deliberately empty room.
        self.assertEqual(ROOMS["open_floor"][0].obstacles, ())

    def test_training_and_evaluation_are_repeatable(self):
        a = evaluate(ticks=55, train_ticks=60, seeds=2)
        b = evaluate(ticks=55, train_ticks=60, seeds=2)
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], "mojum.evaluation.heldout-room/1")
        self.assertEqual(len(a["cases"]), 5)
        self.assertEqual(a["evaluation_seeds"], list(EVAL_SEEDS[:2]))
        self.assertEqual(a["status"], "CONTROLLED_SYNTHETIC_FIXTURE_NOT_GENERAL_INTELLIGENCE")

    def test_short_horizon_policy_does_not_mutate_transferred_snapshot(self):
        from mojum.learned_policy import TabularAxisPolicy
        policy = TabularAxisPolicy()
        training = policy.snapshot()
        before = copy.deepcopy(training)
        room = ROOMS["shifted_barrier"][0]
        a = _episode(room, "transfer_frozen", EVAL_SEEDS[0], training, 30)
        b = _episode(room, "transfer_frozen", EVAL_SEEDS[0], training, 30)
        self.assertEqual(a, b)
        self.assertEqual(a["evaluation_updates"], 0)
        self.assertEqual(training, before)
        self.assertGreaterEqual(a["final_goal_distance_m"], 0)

    def test_detour_is_physically_possible_but_policy_cannot_make_it(self):
        """Prove reachability with an explicit witness, not the learned policy."""
        room = ROOMS["detour_required"][0]
        state = initial_state(room, *START)
        waypoints = ((1.0, 3.9), (3.5, 3.9), GOAL)
        for tick, (x, y) in enumerate(waypoints, start=1):
            state, observation = advance(
                room, state, tick=tick, dx=x - state.x, dy=y - state.y,
                expected_revision=state.revision,
            )
            self.assertFalse(observation["blocked"])
        self.assertAlmostEqual(state.x, GOAL[0])
        self.assertAlmostEqual(state.y, GOAL[1])

    def test_negative_controls_and_frozen_q_no_updates(self):
        result = evaluate(ticks=240, train_ticks=160, seeds=2)
        by_room = {entry["room"]: entry for entry in result["cases"]}
        for label in ("detour_required", "sealed_barrier"):
            for mode in MODES:
                self.assertEqual(
                    by_room[label]["policies"][mode]["goals_reached"], 0,
                    (label, mode),
                )
        for case in by_room.values():
            self.assertEqual(case["policies"]["transfer_frozen"]["mean_new_policy_updates"], 0)
            for mode in MODES:
                self.assertEqual(case["policies"][mode]["episodes"], 2)
                self.assertEqual(len(case["policies"][mode]["per_seed"]), 2)
                for episode in case["policies"][mode]["per_seed"]:
                    self.assertGreaterEqual(episode["blocked_steps"], 0)
                    self.assertGreaterEqual(episode["final_goal_distance_m"], 0)
                    self.assertLessEqual(episode["blocked_steps"], 240)
        # A room with no obstacles should be traversable by every variant.
        for mode in MODES:
            self.assertEqual(by_room["open_floor"]["policies"][mode]["goals_reached"], 2)

    def test_invalid_protocol_inputs_rejected(self):
        for args in (
            {"ticks": 0}, {"ticks": 601}, {"ticks": True},
            {"train_ticks": 0}, {"train_ticks": 601}, {"train_ticks": "10"},
            {"seeds": 0}, {"seeds": 5}, {"seeds": True},
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    evaluate(**args)

    def test_cli_records_full_seed_by_room_matrix_without_claiming_generalization(self):
        cmd = [sys.executable, str(ROOT / "examples/evaluate_transfer_r06.py"),
               "--ticks", "180", "--train-ticks", "160", "--seeds", "3"]
        output = json.loads(subprocess.check_output(cmd, text=True))
        self.assertEqual(output["evaluation_ticks"], 180)
        self.assertEqual(output["evaluation_seeds"], list(EVAL_SEEDS[:3]))
        self.assertEqual(len(output["cases"]), 5)
        for case in output["cases"]:
            self.assertEqual(set(case["policies"]), set(MODES))
        summary = {
            entry["room"]: {
                mode: {
                    "success": metrics["goals_reached"],
                    "mean_blocked": metrics["mean_blocked_steps"],
                    "mean_distance": metrics["mean_final_goal_distance_m"],
                }
                for mode, metrics in entry["policies"].items()
            }
            for entry in output["cases"]
        }
        print("MOJUM_R06_HELDOUT_SUMMARY=" + json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
