"""Opt-in R0.14: real 4cm virtual motor outcomes in one Aquarium journal."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError
from mojum.noosphere import NoosphereError
from mojum.sensory_play import Toy, ToyScene
from mojum.temporal_outcomes import RangeOutcomeMemory, TemporalOutcomeError, SCHEMA
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class TemporalOutcomeAquariumTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.db = Path(d.name) / "dante-one.sqlite3"
        self.other = Path(d.name) / "dante-control.sqlite3"

    def baby(self, db=None, **kwargs):
        return Aquarium(
            db or self.db, SEED, GRAPH, exploration_mode="goal_free",
            sensory_mode="toy_probe", temporal_mode="range_outcome", **kwargs,
        )

    def test_one_private_frame_contains_measured_virtual_outcome(self):
        with self.baby(neural_mode="disabled") as baby:
            self.assertEqual(baby.temporal_model.updates, 0)
            frame = baby.step()
            data = frame["temporal_range"]
            self.assertEqual(data["schema"], SCHEMA)
            self.assertEqual(data["before"], frame["toy_contact"]["observation"])
            self.assertEqual(data["action"], frame["developmental"]["intent"]["action"])
            self.assertEqual(data["measured_outcome"]["measured_blocked"],
                             frame["world"]["blocked"])
            self.assertEqual(data["measured_outcome"]["total_updates"], 1)
            self.assertEqual(data["predicted_outcome"]["observations"], 0)
            self.assertIsNone(data["predicted_outcome"]["blocked"])
            self.assertEqual(data["after"]["tick"], data["before"]["tick"] + 1)
            self.assertEqual(baby.temporal_model.updates, 1)
            self.assertEqual(baby.store.count(), 2)
            self.assertEqual(baby.store.dataset()["projection"]["items"], [])
            private = baby.store.dataset(include_nonpublic=True)["projection"]["items"]
            self.assertEqual(private[-1]["event"]["payload"], frame)
            self.assertEqual(frame["physical_model"], "NONE_FIXTURE_ONLY")
            self.assertIsNone(frame["brain"]["action"])

    def test_applied_displacement_is_zero_when_blocked_but_intent_nonzero(self):
        wall = Room(4., 4., ((1.01, 1.96, .01, .08),))
        with self.baby(room=wall, neural_mode="disabled") as blocked, self.baby(
                self.other, room=Room(4., 4.), neural_mode="disabled") as free:
            witnessed = False
            for _ in range(20):
                left, right = blocked.step(), free.step()
                if (left["movement_request"]["dx"] > 0
                        and left["world"]["blocked"]):
                    observed = left["temporal_range"]
                    baseline = right["temporal_range"]
                    self.assertEqual(observed["before"]["reflectance_rgb"],
                                     baseline["before"]["reflectance_rgb"])
                    self.assertEqual(observed["before"]["range_bucket_10cm"],
                                     baseline["before"]["range_bucket_10cm"])
                    self.assertGreater(observed["requested_dx_dy"][0], 0)
                    self.assertEqual(observed["applied_dx_dy"], [0.0, 0.0])
                    self.assertFalse(baseline["measured_outcome"]["measured_blocked"])
                    self.assertTrue(observed["measured_outcome"]["measured_blocked"])
                    predicted_block = blocked.temporal_model.predict(
                        observed["before"], observed["action"])
                    predicted_free = free.temporal_model.predict(
                        observed["before"], observed["action"])
                    self.assertTrue(predicted_block["blocked"])
                    self.assertFalse(predicted_free["blocked"])
                    witnessed = True
                    break
            self.assertTrue(witnessed, "a real AABB wall must block a 4cm action")

    def test_actual_motion_bounds_and_numeric_oracle_isolation(self):
        with self.baby(neural_mode="disabled") as baby:
            for _ in range(22):
                frame = baby.step()
                record = frame["temporal_range"]
                intended = record["requested_dx_dy"]
                applied = record["applied_dx_dy"]
                self.assertLessEqual(sum(abs(v) for v in intended), .04)
                self.assertLessEqual(sum(abs(v) for v in applied), .0400000001)
                self.assertAlmostEqual(applied[0],
                    frame["world"]["position"][0] -
                    frame["developmental"]["observation"]["position_xy"][0])
                if frame["world"]["blocked"]:
                    self.assertEqual(applied, [0.0, 0.0])
                self.assertEqual(set(record["predicted_outcome"]), {
                    "observations", "next_bucket", "blocked"})
            encoded = json.dumps(baby.temporal_model.snapshot())
            for secret in ("softness", "spectator.toy", "rgb", "220", "70", "30"):
                self.assertNotIn(secret, encoded)

    def test_numeric_history_ignores_new_rgb_and_hidden_spectator_id(self):
        toy1 = ToyScene(toys=(Toy("experiment.red", 1.2, 2., .15,
                                  (220, 70, 30), .4),))
        toy2 = ToyScene(toys=(Toy("experiment.green", 1.2, 2., .15,
                                  (20, 210, 95), .9),))
        with self.baby(sensory_scene=toy1, neural_mode="disabled") as a, \
                self.baby(self.other, sensory_scene=toy2,
                          neural_mode="disabled") as b:
            for _ in range(22):
                left, right = a.step(), b.step()
                self.assertEqual(left["temporal_range"]["action"],
                                 right["temporal_range"]["action"])
                self.assertEqual(left["temporal_range"]["predicted_outcome"],
                                 right["temporal_range"]["predicted_outcome"])
                self.assertEqual(left["temporal_range"]["measured_outcome"],
                                 right["temporal_range"]["measured_outcome"])
            self.assertEqual(a.temporal_model.snapshot(),
                             b.temporal_model.snapshot())
            self.assertNotEqual(a.sensory_bridge.model.snapshot(),
                                b.sensory_bridge.model.snapshot())

    def test_exact_same_journal_replays_temporal_and_future_frame(self):
        with self.baby(neural_mode="disabled") as a, self.baby(
                self.other, neural_mode="disabled") as control:
            for _ in range(26):
                self.assertEqual(a.step(), control.step())
            state = a.checkpoint()
            future = control.step()
        with self.baby(neural_mode="disabled") as restarted:
            self.assertEqual(restarted.checkpoint(), state)
            self.assertEqual(restarted.step(), future)
            self.assertEqual(restarted.temporal_model.updates, 27)
            self.assertEqual(restarted.store.count(), 28)

    def test_crash_after_sqlite_append_reconstructs_unadopted_temporal(self):
        with self.baby(neural_mode="disabled") as a:
            frame, _, _ = a._calculate(None, None)
            a._append_record("aquarium.frame", "frame-000000000000",
                             frame, 0, a.genome.revision)
            self.assertEqual(a.temporal_model.updates, 0)
            with self.assertRaises(AquariumError):
                a.checkpoint()
        with self.baby(neural_mode="disabled") as reconstructed:
            self.assertEqual(reconstructed.tick, 1)
            self.assertEqual(reconstructed.temporal_model.updates, 1)
            self.assertEqual(reconstructed.store.count(), 2)

    def test_failed_commit_leaves_sensor_and_policy_memory_unchanged(self):
        with self.baby(neural_mode="disabled") as a:
            previous = a.checkpoint()
            with patch.object(a, "_append_record",
                              side_effect=NoosphereError("injected")):
                with self.assertRaises(NoosphereError):
                    a.step()
            self.assertEqual(a.checkpoint(), previous)
            self.assertEqual(a.store.count(), 1)

    def test_stale_writer_does_not_adopt_outcome_or_export_mixed_checkpoint(self):
        with self.baby(neural_mode="disabled") as a, self.baby(
                neural_mode="disabled") as stale:
            before = stale.temporal_model.snapshot()
            a.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.temporal_model.snapshot(), before)
            self.assertEqual(stale.tick, 0)
            with self.assertRaises(AquariumError):
                stale.checkpoint()

    def test_forged_temporal_evidence_rejected_before_model_adoption(self):
        with self.baby(neural_mode="disabled") as a:
            frame, world, _ = a._calculate(None, None)
            original = a.temporal_model.snapshot()
            forged = copy.deepcopy(frame)
            forged["temporal_range"]["predicted_outcome"]["observations"] = 42
            with self.assertRaises(AquariumError):
                a._adopt_temporal(forged, world)
            self.assertEqual(a.temporal_model.snapshot(), original)
            self.assertEqual(a.store.count(), 1)

    def test_existing_modes_keep_journal_schema_and_reject_reinterpretation(self):
        with Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free",
                      sensory_mode="toy_probe") as original:
            data = original.step()
            self.assertNotIn("temporal_range", data)
            self.assertNotIn("temporal_range_state", original.checkpoint())
        with self.assertRaises(AquariumError):
            self.baby()
        with self.baby(self.other, neural_mode="disabled") as new:
            self.assertIn("temporal_range", new.step())
        with self.assertRaises(AquariumError):
            Aquarium(self.other, SEED, GRAPH,
                     exploration_mode="goal_free", sensory_mode="toy_probe",
                     neural_mode="disabled")

    def test_bad_mode_and_external_dx_fail_before_any_birth(self):
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, temporal_mode="range_outcome")
        with self.assertRaises(AquariumError):
            Aquarium(self.db, SEED, GRAPH, temporal_mode="garbage")
        self.assertFalse(self.db.exists())
        with self.baby(neural_mode="disabled") as baby:
            state = baby.checkpoint()
            for args in ({"dx": None}, {"dx": .04}, {"dy": 0.}):
                with self.assertRaises(AquariumError):
                    baby.step(**args)
            self.assertEqual(baby.checkpoint(), state)

    def test_snapshot_validation_and_causal_input_rejection(self):
        model = RangeOutcomeMemory()
        with self.baby(self.other, neural_mode="disabled") as a:
            record = a.step()["temporal_range"]
        before = record["before"]
        after = record["after"]
        action = record["action"]
        prior = model.predict(before, action)
        model.observe(before, action, after, False, prior)
        good = model.snapshot()
        self.assertEqual(RangeOutcomeMemory.from_snapshot(good).snapshot(), good)
        invalids = []
        for key in ("9"*1000+"|x+", "7|x+", "none|teleport"):
            bad = copy.deepcopy(good)
            bad["histograms"] = {key: {"none|0": 1}}
            invalids.append(bad)
        bad = copy.deepcopy(good)
        bad["updates"] = 4
        invalids.append(bad)
        bad = copy.deepcopy(good)
        key = next(iter(bad["histograms"]))
        bad["histograms"][key] = {"1|1": True}
        invalids.append(bad)
        for bad in invalids:
            with self.assertRaises(TemporalOutcomeError):
                RangeOutcomeMemory.from_snapshot(bad)
        forged = copy.deepcopy(after)
        forged["tick"] = before["tick"]
        snapshot = model.snapshot()
        with self.assertRaises(TemporalOutcomeError):
            model.observe(before, action, forged, False,
                          model.predict(before, action))
        self.assertEqual(model.snapshot(), snapshot)

    def test_cli_resume_and_preflight_validation_without_sqlite_side_effect(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [sys.executable, "-m", "mojum.aquarium",
                   "--db", str(self.db), "--ticks", "2",
                   "--exploration-mode", "goal_free",
                   "--sensory-mode", "toy_probe",
                   "--temporal-mode", "range_outcome",
                   "--neural-mode", "disabled"]
        first = json.loads(subprocess.check_output(
            command, env=env, cwd=ROOT, text=True, timeout=30))
        second = json.loads(subprocess.check_output(
            command, env=env, cwd=ROOT, text=True, timeout=30))
        self.assertEqual(first["temporal_range_state"]["updates"], 2)
        self.assertEqual(second["temporal_range_state"]["updates"], 4)
        self.assertEqual(second["stored_events"], 5)
        invalid_path = self.db.with_name("not-created.sqlite3")
        rejected = subprocess.run([
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(invalid_path), "--ticks", "1",
            "--temporal-mode", "range_outcome"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(invalid_path.exists())


if __name__ == "__main__":
    unittest.main()
