"""R0.13: goal-free choice belongs to the *canonical* one-journal Aquarium."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.developmental_policy import (
    DevelopmentError, Explorer, Proprioception, SCHEMA as DEVELOPMENT_SCHEMA,
)
from mojum.noosphere import NoosphereError
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class CanonicalDevelopmentalAquariumTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.db = Path(t.name) / "one-organism.sqlite3"
        self.second_db = Path(t.name) / "second.sqlite3"

    def baby(self, db=None, **kw):
        return Aquarium(db or self.db, SEED, GRAPH,
                        exploration_mode="goal_free", **kw)

    def test_real_sensory_action_outcome_learning_is_one_private_event(self):
        with self.baby() as a:
            frame = a.step()
            self.assertEqual(a.tick, 1)
            d = frame["developmental"]
            self.assertEqual(d["schema"], DEVELOPMENT_SCHEMA)
            self.assertEqual(d["observation"]["tick"], 0)
            self.assertEqual(d["observation"]["world_revision"],
                             d["intent"]["expected_revision"])
            self.assertEqual(d["intent"]["dx"], frame["movement_request"]["dx"])
            self.assertEqual(d["intent"]["dy"], frame["movement_request"]["dy"])
            self.assertEqual(d["learning"]["measured_blocked"], frame["world"]["blocked"])
            self.assertEqual(d["learning"]["total_updates"], 1)
            self.assertEqual(a.explorer.updates, 1)
            self.assertEqual(a.store.count(), 2)
            self.assertEqual(a.store.dataset()["projection"]["items"], [])
            persisted = a.store.dataset(include_nonpublic=True)["projection"]["items"][-1]
            self.assertEqual(persisted["event"]["payload"], frame)
            self.assertEqual(frame["physical_model"], "NONE_FIXTURE_ONLY")
            self.assertIsNone(frame["brain"]["action"])

    def test_policy_receives_no_privileged_wall_geometry_or_object_identity(self):
        with self.baby() as a:
            frame = a.step()
            cue = frame["developmental"]["observation"]
            self.assertEqual(set(cue), {
                "schema", "tick", "world_revision", "position_xy",
                "heartbeat_pulse", "source",
            })
            self.assertNotIn("obstacles", cue)
            self.assertNotIn("softness", cue)
            self.assertNotIn("goal", cue)
            self.assertNotIn("toys", cue)
            self.assertNotIn("x", frame["developmental"]["intent"])
            intent = frame["developmental"]["intent"]
            self.assertLessEqual(abs(intent["dx"]) + abs(intent["dy"]), .04)
            self.assertEqual(frame["developmental"]["motor_capability"],
                             "SYNTHETIC_POINT_ROOM_ONLY")

    def test_goal_free_loop_restarts_with_identical_policy_and_future_intent(self):
        with self.baby() as left, self.baby(self.second_db) as right:
            for _ in range(40):
                self.assertEqual(left.step(), right.step())
            saved = left.checkpoint()
            self.assertEqual(saved, right.checkpoint())
            self.assertEqual(saved["developmental_state"]["updates"], 40)
            next_expected = right.step()
        with self.baby() as restored:
            self.assertEqual(restored.checkpoint(), saved)
            self.assertEqual(restored.step(), next_expected)
            self.assertEqual(restored.checkpoint()["developmental_state"]["updates"], 41)

    def test_externally_scripted_movement_rejected_before_journal_commit(self):
        with self.baby() as a:
            initial = a.checkpoint()
            for kwargs in ({"dx": .02}, {"dy": 0}, {"dx": 0., "dy": 0.}):
                with self.subTest(kwargs=kwargs), self.assertRaises(AquariumError):
                    a.step(**kwargs)
            self.assertEqual(a.checkpoint(), initial)
            self.assertEqual(a.store.count(), 1)

    def test_developmental_mode_cannot_reinterpret_old_journal_or_reverse(self):
        with self.baby() as a:
            a.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH)
        with Aquarium(self.second_db, SEED, GRAPH) as old:
            old.step()
            self.assertNotIn("developmental_state", old.checkpoint())
        with self.assertRaises(AquariumError):
            self.baby(self.second_db)

    def test_stale_concurrent_writer_never_advances_policy_ram(self):
        with self.baby() as left, self.baby() as right:
            before = right.checkpoint()
            left.step()
            with self.assertRaises(NoosphereError):
                right.step()
            # An externally advanced durable head must never be combined
            # with this stale writer's unadvanced world/policy as a checkpoint.
            self.assertEqual(right.tick, before["tick"])
            self.assertEqual(right.world.revision, before["world_revision"])
            self.assertEqual(right.explorer.snapshot(), before["developmental_state"])
            self.assertEqual(right.learner.snapshot(), before["neural_state"])
            self.assertNotEqual(right.store.head(), before["journal_head"])
            with self.assertRaisesRegex(AquariumError, "stale or unadopted"):
                right.checkpoint()
            self.assertEqual(right.explorer.updates, 0)

    def test_restart_recovers_append_before_ram_adoption(self):
        with self.baby() as a:
            payload, _, _ = a._calculate(None, None)
            a._append_record("aquarium.frame", "frame-000000000000",
                             payload, 0, a.genome.revision)
            self.assertEqual(a.tick, 0)
            self.assertEqual(a.explorer.updates, 0)
        with self.baby() as a:
            self.assertEqual(a.tick, 1)
            self.assertEqual(a.explorer.updates, 1)
            self.assertEqual(a.store.count(), 2)

    def test_no_action_or_model_claim_in_default_old_mode(self):
        with Aquarium(self.db, SEED, GRAPH) as a:
            frame = a.step()
            self.assertNotIn("developmental", frame)
            self.assertEqual(frame["movement_request"], {"dx": .02, "dy": 0.})
            self.assertNotIn("developmental_state", a.checkpoint())
            self.assertIsNone(a.explorer)

    def test_shadow_learner_can_coexist_without_motor_authority(self):
        with self.baby(neural_mode="shadow") as a:
            records = [a.step() for _ in range(4)]
            self.assertEqual(a.explorer.updates, 4)
            self.assertEqual(a.learner.updates, 3)
            self.assertEqual(records[-1]["learning_observer"]["motor_intention"], None)
            self.assertEqual(a.checkpoint()["neural_mode"], "shadow")

    def test_two_actual_rooms_diverge_only_after_different_collision_experience(self):
        # A walled-in sandbox and an open sandbox share seed, heartbeat and
        # starting proprioception, but produce different *measured* collisions.
        walls = (
            (1.01, 1.98, .01, .04),
            (.98, 1.98, .01, .04),
            (.98, 2.01, .04, .01),
            (.98, 1.98, .04, .01),
        )
        with self.baby(room=Room(4, 4, walls), neural_mode="disabled") as confined, \
                self.baby(self.second_db, room=Room(4, 4),
                          neural_mode="disabled") as open_room:
            observed_different_outcome = False
            later_action_diverged = False
            for _ in range(40):
                a, b = confined.step(), open_room.step()
                left = a["developmental"]["intent"]["action"]
                right = b["developmental"]["intent"]["action"]
                if not observed_different_outcome:
                    self.assertEqual(left, right)
                elif left != right:
                    later_action_diverged = True
                if a["world"]["blocked"] != b["world"]["blocked"]:
                    observed_different_outcome = True
            self.assertTrue(observed_different_outcome)
            self.assertTrue(later_action_diverged)
            self.assertEqual(confined.explorer.updates, 40)
            self.assertEqual(open_room.explorer.updates, 40)

    def test_policy_action_changes_after_distinct_measured_experience(self):
        success = Explorer()
        blocked = Explorer()
        for policy, result in ((success, False), (blocked, True)):
            cue = Proprioception(0, "a" * 64, 1., 2., False)
            policy.observe(cue, policy.decide(cue), result)
        for tick in range(1, 5):
            cue = Proprioception(tick, "a" * 64, 1., 2., False)
            for policy in (success, blocked):
                policy.observe(cue, policy.decide(cue), False)
        next_cue = Proprioception(5, "a" * 64, 1., 2., False)
        self.assertNotEqual(success.decide(next_cue)["action"],
                            blocked.decide(next_cue)["action"])
        self.assertEqual(success.decide(next_cue)["action"], "rest")
        self.assertEqual(blocked.decide(next_cue)["action"], "x+")

    def test_snapshot_and_causal_input_validation_fail_closed(self):
        learner = Explorer()
        cue = Proprioception(0, "f" * 64, 1., 2., True)
        intent = learner.decide(cue)
        with self.assertRaises(DevelopmentError):
            learner.observe(cue, intent, "not-measured")
        learner.observe(cue, intent, False)
        snap = learner.snapshot()
        self.assertEqual(Explorer.from_snapshot(snap).snapshot(), snap)
        corrupted = copy.deepcopy(snap)
        corrupted["updates"] = 5
        with self.assertRaises(DevelopmentError):
            Explorer.from_snapshot(corrupted)
        with self.assertRaises(DevelopmentError):
            learner.decide(cue)

    def test_bad_dev_mode_rejected(self):
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="objective")
