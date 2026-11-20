"""Mojum R0.5: bounded tabular learning and journal replay acceptance tests."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.learned_policy import (
    PolicyError, SCHEMA, TabularAxisPolicy, gate_fixture_proposal,
)
from mojum.noosphere import NoosphereError
from mojum.world_room import Room, advance, initial_state


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def sensory(at=0, pulse=False):
    return {"kind": "interoception", "observed_at_ns": at,
            "heartbeat": {"pulse": pulse}}


class TabularPolicyUnitTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, .1, 2),))
        self.world = initial_state(self.room, 1.99, 2.0)
        self.goal = (3.5, 3.5)

    def test_actual_blocked_reward_updates_q_and_changes_greedy_choice(self):
        policy = TabularAxisPolicy()
        with patch.object(policy, "_random", return_value=0xffffffff):
            first = policy.propose(self.room, self.world, sensory(), self.goal)
        self.assertEqual(first["axis"], "x")  # tie: first action
        self.assertEqual(first["motor_command"], None)
        world, result = advance(self.room, self.world, tick=1,
                                dx=first["dx"], dy=first["dy"],
                                expected_revision=self.world.revision)
        self.assertTrue(result["blocked"])
        feedback = policy.learn(self.room, self.world, world, result, self.goal, first)
        self.assertEqual(feedback["reward"], -1.0)
        self.assertEqual(feedback["q_after"], -0.25)
        self.assertEqual(policy.updates, 1)
        with patch.object(policy, "_random", return_value=0xffffffff):
            second = policy.propose(self.room, world, sensory(at=1), self.goal)
        self.assertEqual(second["axis"], "y")  # learned, not hard-coded collision override
        self.assertGreater(second["dy"], 0)

    def test_progress_reward_is_positive_not_a_predetermined_label(self):
        policy = TabularAxisPolicy()
        room = Room(4, 4)
        state = initial_state(room, 1.0, 1.0)
        proposal = policy.propose(room, state, sensory(), (3.0, 3.0))
        after, obs = advance(room, state, tick=1, dx=proposal["dx"],
                             dy=proposal["dy"], expected_revision=state.revision)
        feedback = policy.learn(room, state, after, obs, (3.0, 3.0), proposal)
        self.assertTrue(feedback["trained"])
        self.assertGreater(feedback["reward"], 0)
        self.assertNotEqual(feedback["q_after"], 0)

    def test_identical_experience_produces_identical_model(self):
        left, right = TabularAxisPolicy(), TabularAxisPolicy()
        room = Room(4, 4)
        a, b = initial_state(room, 1, 1), initial_state(room, 1, 1)
        for tick in range(60):
            frame = sensory(at=tick, pulse=(tick % 9 == 0))
            p = left.propose(room, a, frame, (3, 3))
            q = right.propose(room, b, frame, (3, 3))
            self.assertEqual(p, q)
            na, oa = advance(room, a, tick=tick + 1, dx=p["dx"], dy=p["dy"],
                             expected_revision=a.revision)
            nb, ob = advance(room, b, tick=tick + 1, dx=q["dx"], dy=q["dy"],
                             expected_revision=b.revision)
            self.assertEqual(left.learn(room, a, na, oa, (3, 3), p),
                             right.learn(room, b, nb, ob, (3, 3), q))
            a, b = na, nb
        self.assertEqual(left.snapshot(), right.snapshot())
        self.assertEqual(TabularAxisPolicy.from_snapshot(left.snapshot()).snapshot(),
                         left.snapshot())

    def test_snapshot_is_deep_copy_and_invalid_state_rejected(self):
        policy = TabularAxisPolicy()
        snapshot = policy.snapshot()
        snapshot["rng_state"] = 0
        self.assertEqual(policy.snapshot()["rng_state"], 1979)
        corrupt = policy.snapshot()
        corrupt["updates"] = 8
        with self.assertRaises(PolicyError):
            TabularAxisPolicy.from_snapshot(corrupt)
        for malicious in (
            {"schema": SCHEMA, "rng_state": True, "updates": 0, "table": {}},
            {"schema": SCHEMA, "rng_state": 1979, "updates": 0,
             "table": {"0:0": {"values": [float("nan"), 0], "visits": [0, 0]}}},
            {"schema": SCHEMA, "rng_state": 1979, "updates": 0,
             "table": {"0:0": {"values": [0, 0], "visits": [1, 0]}}},
            {"schema": "wrong", "rng_state": 1979, "updates": 0, "table": {}},
        ):
            with self.subTest(snapshot=repr(malicious)[:60]):
                with self.assertRaises(PolicyError):
                    TabularAxisPolicy.from_snapshot(malicious)

    def test_world_only_gate_rejects_unsafe_proposals(self):
        policy = TabularAxisPolicy()
        valid = policy.propose(self.room, self.world, sensory(), self.goal)
        self.assertEqual(gate_fixture_proposal(self.room, self.world, sensory(),
                                               self.goal, valid),
                         (valid["dx"], valid["dy"]))
        invalid = []
        for key, value in (
            ("dx", float("inf")), ("dx", 1.0),
            ("dy", 1.0), ("actuation", "REAL_HARDWARE"),
            ("motor_command", {"joint": "shoulder", "angle": 20}),
            ("world_revision", "stale"), ("observed_at_ns", 3),
            ("goal", [1, 1]), ("model_updates", True),
            ("axis", "z"), ("schema", "unreviewed"),
        ):
            p = copy.deepcopy(valid)
            p[key] = value
            invalid.append(p)
        for bad in invalid:
            with self.subTest(bad=repr(bad)[:80]):
                with self.assertRaises((PolicyError, ValueError)):
                    gate_fixture_proposal(self.room, self.world, sensory(), self.goal, bad)

    def test_invalid_sensory_and_goal_do_not_train(self):
        policy = TabularAxisPolicy()
        before = policy.snapshot()
        for pulse in (1, "yes", None):
            with self.assertRaises(PolicyError):
                policy.propose(self.room, self.world, sensory(pulse=pulse), self.goal)
        for goal in ((2, 2), (float("nan"), 1), (True, 1)):
            with self.assertRaises(ValueError):
                policy.propose(self.room, self.world, sensory(), goal)
        self.assertEqual(policy.snapshot(), before)

    def test_feedback_must_match_world_observation(self):
        policy = TabularAxisPolicy()
        p = policy.propose(self.room, self.world, sensory(), self.goal)
        after, obs = advance(self.room, self.world, tick=1, dx=p["dx"],
                             dy=p["dy"], expected_revision=self.world.revision)
        bad = dict(obs, blocked=not obs["blocked"])
        with self.assertRaises(PolicyError):
            policy.learn(self.room, self.world, after, bad, self.goal, p)
        self.assertEqual(policy.updates, 0)

    def test_goal_reached_is_idle_without_learning(self):
        room = Room(4, 4)
        start = initial_state(room, 1, 1)
        policy = TabularAxisPolicy()
        p = policy.propose(room, start, sensory(), (1, 1))
        self.assertEqual(p["axis"], "idle")
        after, obs = advance(room, start, tick=1, dx=0, dy=0, expected_revision=start.revision)
        self.assertFalse(policy.learn(room, start, after, obs, (1, 1), p)["trained"])
        self.assertEqual(policy.updates, 0)


class LearnedAquariumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "learned.sqlite3"

    def open(self, **kw):
        return Aquarium(self.db, SEED, GRAPH, control_mode="learned_fixture", **kw)

    def test_full_training_is_journaled_and_brain_remains_placeholder(self):
        with self.open() as a:
            frames = [a.step() for _ in range(80)]
            self.assertEqual(a.policy.updates, 80)
            self.assertTrue(any(f["world"]["blocked"] for f in frames))
            self.assertTrue(all(f["policy_learning"]["trained"] for f in frames))
            self.assertTrue(any(f["policy_learning"]["q_after"] != 0 for f in frames))
            self.assertTrue(all(f["controller"]["actuation"] == "SYNTHETIC_POINT_ROOM_ONLY" for f in frames))
            self.assertTrue(all(f["controller"]["motor_command"] is None for f in frames))
            self.assertTrue(all(f["brain"]["status"] == "UNIMPLEMENTED" for f in frames))
            self.assertTrue(all(f["physical_model"] == "NONE_FIXTURE_ONLY" for f in frames))
            rows = a.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(rows[-1]["event"]["payload"]["policy_learning"],
                             frames[-1]["policy_learning"])
            self.assertEqual(len(a.store.dataset()["projection"]["items"]), 0)

    def test_restart_replays_exact_policy_weights_and_rng(self):
        separate = self.db.with_name("uninterrupted.sqlite3")
        with self.open() as a:
            for _ in range(60):
                a.step()
            saved = a.checkpoint()
        with self.open() as a:
            self.assertEqual(a.checkpoint(), saved)
            for _ in range(60):
                a.step()
            resumed = a.checkpoint()
        with Aquarium(separate, SEED, GRAPH, control_mode="learned_fixture") as b:
            for _ in range(120):
                b.step()
            self.assertEqual(b.checkpoint(), resumed)
        with self.open() as again:
            self.assertEqual(again.checkpoint(), resumed)

    def test_stale_sqlite_cas_preserves_training_state(self):
        with self.open() as a, self.open() as b:
            before = (b.tick, b.world, b.policy.snapshot(),
                      b.learner.snapshot(), b._last_feedback)
            a.step()
            with self.assertRaises(NoosphereError):
                b.step()
            self.assertEqual((b.tick, b.world, b.policy.snapshot(),
                              b.learner.snapshot(), b._last_feedback), before)

    def test_manual_command_cannot_override_learned_policy(self):
        with self.open() as a:
            before = a.checkpoint()
            for values in ({"dx": .1}, {"dy": 0}, {"dx": 0, "dy": 0}):
                with self.assertRaises(AquariumError):
                    a.step(**values)
            self.assertEqual(a.checkpoint(), before)

    def test_mode_and_goal_immutable_without_migration(self):
        with self.open() as a:
            a.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, control_mode="reactive_fixture")
        with self.assertRaises(AquariumError):
            self.open(goal=(3.0, 3.0))

    def test_cli_resumes_learning_state(self):
        cmd = [sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
               "--ticks", "12", "--control-mode", "learned_fixture"]
        a = json.loads(subprocess.check_output(cmd, text=True))
        b = json.loads(subprocess.check_output(cmd, text=True))
        self.assertEqual((a["tick"], b["tick"]), (12, 24))
        self.assertEqual(b["policy_state"]["updates"], 24)
        self.assertEqual(b["stored_events"], 25)
        self.assertIn("NOT_NEURAL_BRAIN", b["status"])


if __name__ == "__main__":
    unittest.main()
