"""R0.14: numeric room-ray observations and measured causal learning in Aquarium."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.developmental_policy import DevelopmentError
from mojum.near_field import (
    ACTIONS, MODEL_SCHEMA, RangeExplorer, RangeSignal,
    SENSE_SCHEMA, sense_room_rays,
)
from mojum.noosphere import NoosphereError
from mojum.world_room import Room, initial_state

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class CanonicalRoomRayLearningTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "range.sqlite3"
        self.other = self.root / "other.sqlite3"

    def child(self, db=None, **kwargs):
        return Aquarium(db or self.db, SEED, GRAPH,
                        exploration_mode="goal_free_rays", **kwargs)

    def test_environment_raycast_reports_quantized_axis_distances_only(self):
        room = Room(4, 4, ((1.18, 1.95, .1, .1),))
        state = initial_state(room, 1., 2.)
        self.assertEqual(sense_room_rays(room, state), (3, 12, 12, 12))
        signal = RangeSignal(0, state.revision, 1., 2., False,
                             sense_room_rays(room, state))
        public = signal.public()
        self.assertEqual(public["schema"], SENSE_SCHEMA)
        self.assertEqual(public["range_bucket_5cm_ewns"], [3, 12, 12, 12])
        self.assertEqual(set(public), {
            "schema", "tick", "world_revision", "position_xy",
            "heartbeat_pulse", "range_bucket_5cm_ewns", "source",
        })
        for oracle in ("obstacles", "toy_id", "material", "softness", "geometry",
                       "future", "goal", "object_identity"):
            self.assertNotIn(oracle, public)

    def test_real_a_b_collision_evidence_changes_action_on_identical_numeric_input(self):
        blocked_room = Room(4, 4, ((.965, 1.98, .01, .04),))
        clear_room = Room(4, 4, ((.945, 1.98, .01, .04),))
        with self.child(room=blocked_room, neural_mode="disabled") as blocked, \
                self.child(self.other, room=clear_room,
                           neural_mode="disabled") as clear:
            left = blocked.step()
            right = clear.step()
            left_signal = left["developmental"]["observation"]
            right_signal = right["developmental"]["observation"]
            # Two different hidden wall coordinates produce *the same
            # quantized sensor*, not a class label for obstacle identity.
            self.assertEqual(left_signal["range_bucket_5cm_ewns"],
                             right_signal["range_bucket_5cm_ewns"])
            self.assertEqual(left_signal["position_xy"], right_signal["position_xy"])
            self.assertEqual(left["developmental"]["intent"]["action"], "x-")
            self.assertEqual(right["developmental"]["intent"]["action"], "x-")
            self.assertTrue(left["world"]["blocked"])
            self.assertFalse(right["world"]["blocked"])
            same_future_cue = RangeSignal(
                1, "a" * 64, 1., 2., False,
                tuple(left_signal["range_bucket_5cm_ewns"]),
            )
            learned_blocked = blocked.explorer.decide(same_future_cue)["action"]
            learned_clear = clear.explorer.decide(same_future_cue)["action"]
            self.assertNotEqual(learned_blocked, learned_clear)
            self.assertEqual(learned_clear, "x-")
            # Frozen-policy negative control: a blank learner with the SAME
            # next-tick sensor uses only innate tie rotation (x-).
            frozen = RangeExplorer()
            frozen.last_tick = 0
            self.assertEqual(frozen.decide(same_future_cue)["action"], "x-")
            self.assertNotEqual(learned_blocked, "x-")
            self.assertNotEqual(learned_clear, frozen.decide(same_future_cue)["action"])
            self.assertNotEqual(blocked.explorer.snapshot(),
                                clear.explorer.snapshot())
            self.assertEqual(blocked.store.count(), 2)
            self.assertEqual(clear.store.count(), 2)

    def test_one_canonical_private_journal_and_exact_restart_replay(self):
        with self.child() as first, self.child(self.other) as reference:
            for _ in range(45):
                left = first.step()
                right = reference.step()
                self.assertEqual(left, right)
            saved = first.checkpoint()
            self.assertEqual(saved, reference.checkpoint())
            self.assertEqual(saved["developmental_state"]["schema"], MODEL_SCHEMA)
            self.assertEqual(saved["developmental_state"]["updates"], 45)
            self.assertEqual(first.store.count(), 46)
            self.assertEqual(first.store.dataset()["projection"]["items"], [])
            self.assertEqual(first.store.dataset(include_nonpublic=True)["projection"]
                             ["items"][-1]["event"]["payload"], left)
            expected_next = reference.step()
        with self.child() as reopened:
            self.assertEqual(reopened.checkpoint(), saved)
            self.assertEqual(reopened.step(), expected_next)
            self.assertEqual(reopened.tick, 46)

    def test_live_state_is_unchanged_after_stale_writer_cas(self):
        with self.child() as first, self.child() as stale:
            prior = stale.checkpoint()
            first.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            after = stale.checkpoint()
            for field in ("tick", "sim_time_ns", "genome_revision",
                          "world_revision", "neural_state",
                          "developmental_state"):
                self.assertEqual(prior[field], after[field])
            self.assertEqual(stale.explorer.updates, 0)

    def test_mode_is_version_pinned_no_cross_mode_reinterpretation(self):
        with self.child() as a:
            a.step()
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free")
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="scripted")
        with self.child() as reopened:
            self.assertEqual(reopened.tick, 1)

    def test_out_of_band_input_does_not_create_or_advance_an_organism(self):
        with self.child() as child:
            baseline = child.checkpoint()
            for args in ({"dx": 0.0}, {"dy": 0.0},
                         {"dx": None}, {"dy": None}):
                with self.subTest(args=args), self.assertRaises(AquariumError):
                    child.step(**args)
                self.assertEqual(child.checkpoint(), baseline)
                self.assertEqual(child.store.count(), 1)
            child.step()
            self.assertEqual(child.explorer.updates, 1)

    def test_range_model_rejects_noncanonical_cues_and_counters(self):
        for rays in (None, [0, 1, 2, 3], (True, 0, 1, 2),
                     (-1, 1, 2, 3), (13, 1, 2, 3),
                     (0, 1, 2), (0, 1, 2, 3, 4)):
            with self.subTest(rays=rays), self.assertRaises(DevelopmentError):
                RangeSignal(0, "a" * 64, 1., 2., False, rays)
        learner = RangeExplorer()
        s = RangeSignal(0, "a" * 64, 1., 2., False, (0, 12, 12, 12))
        learner.observe(s, learner.decide(s), True)
        good = learner.snapshot()
        self.assertEqual(RangeExplorer.from_snapshot(good).snapshot(), good)
        for corrupt in ("model", "zone", "count", "update", "extra"):
            snap = copy.deepcopy(good)
            if corrupt == "model":
                snap["schema"] = "unknown"
            elif corrupt == "zone":
                snap["records"]["2:4|999999999999999999,0,0,0"] = (
                    snap["records"].pop(next(iter(snap["records"]))))
            elif corrupt == "count":
                row = next(iter(snap["records"].values()))
                row["x+"][0] = True
            elif corrupt == "update":
                snap["updates"] = 17
            else:
                snap["undeclared"] = True
            with self.subTest(corrupt=corrupt), self.assertRaises(DevelopmentError):
                RangeExplorer.from_snapshot(snap)

    def test_cli_mode_run_resume_and_no_file_for_bad_override(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "src")
        cmd = [sys.executable, "-m", "mojum.aquarium",
               "--db", str(self.db), "--exploration-mode",
               "goal_free_rays", "--ticks", "2"]
        invalid = subprocess.run(cmd + ["--dx", "1.0"], env=env, text=True,
                                 capture_output=True, check=False)
        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertFalse(self.db.exists())
        first = json.loads(subprocess.check_output(cmd, env=env, text=True))
        self.assertEqual(first["tick"], 2)
        next_ = json.loads(subprocess.check_output(cmd, env=env, text=True))
        self.assertEqual(next_["tick"], 4)
        self.assertEqual(next_["stored_events"], 5)

    def test_legacy_default_and_existing_goal_free_remain_untouched(self):
        with Aquarium(self.db, SEED, GRAPH) as scripted:
            frame = scripted.step()
            self.assertNotIn("developmental", frame)
            self.assertEqual(frame["movement_request"], {"dx": .02, "dy": 0.})
        with Aquarium(self.other, SEED, GRAPH,
                      exploration_mode="goal_free") as old_goal_free:
            frame = old_goal_free.step()
            self.assertEqual(frame["developmental"]["schema"],
                             "mojum.developmental.explorer/1")
            self.assertNotIn("range_bucket_5cm_ewns",
                             frame["developmental"]["observation"])


if __name__ == "__main__":
    unittest.main()
