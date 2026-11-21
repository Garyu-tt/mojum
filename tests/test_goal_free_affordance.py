"""Goal-free numeric affordance learning before Aquarium integration.

The outcome label comes from the deterministic synthetic point-room transition,
not a scripted reward or an external goal coordinate.
"""
import copy
import math
import unittest

from mojum.goal_free_affordance import (
    ACTIONS, MAX_CONTEXTS, AffordanceError, AffordanceMemory,
    SENSOR_SCHEMA, SCHEMA, INTENT_SCHEMA,
)
from mojum.world_room import Room, advance, initial_state


SHA = "a" * 64


def sensor(tick, rgb=None, pulse=False, revision=SHA):
    return {
        "schema": SENSOR_SCHEMA, "tick": tick,
        "world_revision": revision, "visible": rgb is not None,
        "reflectance_rgb": list(rgb) if rgb is not None else None,
        "range_bucket_10cm": 1 if rgb is not None else None,
        "source": "synthetic_local_proximity_not_camera",
    }


def world_outcome(tick, blocked=False):
    return {
        "tick": tick + 1, "revision": SHA,
        "position": [1.0, 2.0], "blocked": blocked,
        "provenance": "deterministic-point-room-fixture/v1",
        "physical_contact": False,
    }


class GoalFreeAffordanceTests(unittest.TestCase):
    def test_untrained_memory_has_no_world_or_object_truth(self):
        child = AffordanceMemory()
        self.assertEqual(child.snapshot(), {
            "schema": SCHEMA, "table": {}, "last_tick": -1, "updates": 0,
        })
        intent = child.choose(sensor(0), False)
        self.assertEqual(intent["schema"], INTENT_SCHEMA)
        self.assertEqual(intent["action"], "x+")
        self.assertEqual((intent["dx"], intent["dy"]), (.02, 0.0))
        self.assertEqual(intent["capability"], "SYNTHETIC_POINT_ROOM_ONLY")
        self.assertFalse(intent["identity_verified"])
        self.assertNotIn("motor_command", intent)
        self.assertNotIn("goal", intent)
        self.assertEqual(child.last_tick, -1)

    def test_same_numeric_cue_diverges_after_different_measured_obstacle(self):
        clear = Room(4, 4)
        obstructed = Room(4, 4, ((1.01, 1.98, .05, .05),))
        a, b = AffordanceMemory(), AffordanceMemory()
        prior = []
        for model, room in ((a, clear), (b, obstructed)):
            state = initial_state(room, 1.0, 2.0)
            cue = sensor(0, rgb=(80, 90, 100), revision=state.revision)
            proposed = model.choose(cue, False)
            self.assertEqual(proposed["action"], "x+")
            actual_state, measured = advance(
                room, state, tick=1, dx=proposed["dx"], dy=proposed["dy"],
                expected_revision=state.revision)
            model.record(cue, False, proposed, measured)
            prior.append((actual_state, measured))
        self.assertFalse(prior[0][1]["blocked"])
        self.assertTrue(prior[1][1]["blocked"])
        self.assertEqual(a.updates, b.updates)
        # At the same future tick and same sensor context, decisions differ
        # ONLY because the organism accumulated different blocked outcomes.
        future = sensor(1, rgb=(80, 90, 100))
        self.assertEqual(a.choose(future, False)["action"], "x+")
        self.assertEqual(b.choose(future, False)["action"], "x-")
        self.assertNotEqual(a.snapshot(), b.snapshot())

    def test_exact_replay_after_snapshot_is_deterministic(self):
        a = AffordanceMemory()
        for tick in range(13):
            frame = sensor(tick, rgb=(40, 100, 200))
            action = a.choose(frame, True)
            a.record(frame, True, action, world_outcome(tick, blocked=tick % 4 == 0))
            if tick == 6:
                saved = a.snapshot()
        b = AffordanceMemory.from_snapshot(saved)
        for tick in range(7, 13):
            frame = sensor(tick, rgb=(40, 100, 200))
            choice = b.choose(frame, True)
            b.record(frame, True, choice, world_outcome(tick, blocked=tick % 4 == 0))
        self.assertEqual(a.snapshot(), b.snapshot())

    def test_one_measured_blockage_can_override_prior_success_streak(self):
        m = AffordanceMemory()
        for tick in range(6):
            frame = sensor(tick)
            command = m.choose(frame, False)
            self.assertEqual(command["action"], "x+")
            m.record(frame, False, command,
                     world_outcome(tick, blocked=tick == 5))
        self.assertEqual(m.updates, 6)
        self.assertNotEqual(m.choose(sensor(6), False)["action"], "x+")

    def test_transaction_candidate_does_not_touch_committed_model(self):
        original = AffordanceMemory()
        before = original.snapshot()
        candidate = AffordanceMemory.from_snapshot(before)
        s = sensor(0)
        candidate.record(s, False, candidate.choose(s, False), world_outcome(0))
        self.assertEqual(original.snapshot(), before)
        self.assertNotEqual(candidate.snapshot(), before)
        # The future Aquarium integration must adopt 'candidate' only after
        # a successful LocalNoosphere append with expected head / CAS.

    def test_rest_acts_as_safe_opt_out_and_advances_only_time(self):
        m = AffordanceMemory()
        rest = m.choose(sensor(0), False, enabled=False)
        self.assertEqual(rest["action"], "rest")
        self.assertEqual((rest["dx"], rest["dy"]), (0.0, 0.0))
        m.record(sensor(0), False, rest, world_outcome(0))
        self.assertEqual(m.snapshot()["table"], {})
        self.assertEqual(m.updates, 0)
        self.assertEqual(m.last_tick, 0)
        self.assertEqual(m.choose(sensor(1), False)["action"], "x-")

    def test_hidden_object_truth_and_extra_keys_rejected(self):
        m = AffordanceMemory()
        for leaked in (
            {"toy_id": "spectator.a"},
            {"softness": .7},
            {"velocity": [.01, 0]},
            {"goal_xy": [3, 2]},
            {"future": True},
        ):
            with self.subTest(leaked=leaked):
                invalid = {**sensor(0, rgb=(2, 3, 4)), **leaked}
                with self.assertRaises(AffordanceError):
                    m.choose(invalid, False)
        self.assertEqual(m.snapshot()["table"], {})

    def test_reject_stale_or_malformed_numeric_cues(self):
        m = AffordanceMemory()
        altered = (
            {"tick": True}, {"tick": 1}, {"visible": "true"},
            {"world_revision": "b" * 63}, {"world_revision": None},
            {"reflectance_rgb": [0, True, 0]},
            {"reflectance_rgb": [1, 2]},
            {"reflectance_rgb": [999, 2, 2]},
            {"range_bucket_10cm": 7},
            {"range_bucket_10cm": False},
            {"source": "camera"},
        )
        for change in altered:
            with self.subTest(change=change), self.assertRaises(AffordanceError):
                m.choose({**sensor(0, rgb=(1, 2, 3)), **change}, False)
        with self.assertRaises(AffordanceError):
            m.choose(sensor(0), 1)

    def test_invalid_outcomes_and_forged_intents_are_atomic(self):
        m = AffordanceMemory()
        s = sensor(0)
        correct = m.choose(s, False)
        before = m.snapshot()
        outcomes = (
            {"blocked": 1}, {"tick": 0}, {"physical_contact": True},
            {"position": [math.nan, 2]}, {"position": [1, True]},
            {"source": "spectator.oracle"},
        )
        for fields in outcomes:
            with self.subTest(fields=fields), self.assertRaises(AffordanceError):
                m.record(s, False, correct, {**world_outcome(0), **fields})
            self.assertEqual(m.snapshot(), before)
        for fields in ({"action": "x-"}, {"dx": 2.0},
                       {"capability": "HARDWARE_MOTOR"},
                       {"world_revision": "b" * 64}):
            with self.subTest(fields=fields), self.assertRaises(AffordanceError):
                m.record(s, False, {**correct, **fields}, world_outcome(0))
            self.assertEqual(m.snapshot(), before)

    def test_impossible_counters_unknown_schema_and_snapshots_fail_closed(self):
        m = AffordanceMemory()
        first = sensor(0, (10, 20, 30))
        m.record(first, False, m.choose(first, False), world_outcome(0))
        good = m.snapshot()
        self.assertEqual(AffordanceMemory.from_snapshot(good).snapshot(), good)
        corruptions = []
        for field, value in (("updates", 2), ("last_tick", -1),
                             ("schema", "untrusted"), ("extra", True),
                             ("updates", True)):
            copy_ = copy.deepcopy(good)
            copy_[field] = value
            corruptions.append(copy_)
        for field, value in (("samples", 2), ("blocked", 2),
                             ("blocked", False), ("samples", 0)):
            copy_ = copy.deepcopy(good)
            key = next(iter(copy_["table"]))
            copy_["table"][key]["x+"][field] = value
            corruptions.append(copy_)
        fake = copy.deepcopy(good)
        fake["table"]["0|999:0:0|1"] = {"x+": {"samples": 1, "blocked": 0}}
        corruptions.append(fake)
        # Regex-admitted "-" with a numeric distance must fail closed,
        # not escape as a raw ValueError from int("-").
        malformed = copy.deepcopy(good)
        malformed["table"] = {"0|-|1": {"x+": {"samples": 1, "blocked": 0}}}
        corruptions.append(malformed)
        for snapshot in corruptions:
            with self.subTest(snapshot=snapshot), self.assertRaises(AffordanceError):
                AffordanceMemory.from_snapshot(snapshot)

    def test_context_budget_rejects_new_unseen_context_without_mutation(self):
        m = AffordanceMemory()
        for tick in range(MAX_CONTEXTS):
            frame = sensor(tick, rgb=(tick, 1, 2))
            intent = m.choose(frame, False)
            m.record(frame, False, intent, world_outcome(tick))
        before = m.snapshot()
        frame = sensor(MAX_CONTEXTS, rgb=(MAX_CONTEXTS, 1, 2))
        proposed = m.choose(frame, False)
        with self.assertRaisesRegex(AffordanceError, "capacity"):
            m.record(frame, False, proposed, world_outcome(MAX_CONTEXTS))
        self.assertEqual(m.snapshot(), before)

    def test_reproducible_witness_runs_in_separate_python_process(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys

        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src")
        cmd = [sys.executable, str(root / "examples" / "affordance_witness.py")]
        first = json.loads(subprocess.check_output(cmd, env=env, text=True))
        second = json.loads(subprocess.check_output(cmd, env=env, text=True))
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "mojum.affordance-witness/1")
        clear = first["clear_room"]["actions"]
        blocked = first["blocked_room"]["actions"]
        self.assertEqual(clear[0]["action"], blocked[0]["action"])
        self.assertFalse(clear[0]["blocked"])
        self.assertTrue(blocked[0]["blocked"])
        self.assertNotEqual(clear[1]["action"], blocked[1]["action"])



if __name__ == "__main__":
    unittest.main()
