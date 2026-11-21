"""R0.14: controlled, optional prediction-to-action path in one Aquarium."""
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
from mojum.developmental_policy import (
    DevelopmentError, Explorer, Proprioception,
)
from mojum.empirical_advisor import (
    ActionAdviceError, MIN_BLOCKED_SAMPLES, SCHEMA, advisory,
)
from mojum.noosphere import NoosphereError
from mojum.sensory_play import Toy, ToyScene
from mojum.temporal_outcomes import RangeOutcomeMemory
from mojum.world_room import Room, advance, initial_state

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def actual_blocking_evidence():
    """Two independently measured virtual 4cm blocked attempts.

    The two positions share a *numeric* 10-cm range bucket, though the
    experimenter knows the underlying geometry. The advisor does not.
    """
    scene = ToyScene(toys=(Toy(
        "private.experiment", 1.72, 2., .15, (210, 70, 40), .6,
    ),))
    room = Room(4., 4., ((1.41, 1.95, .01, .10),))
    memory = RangeOutcomeMemory()
    sensors = []
    for x in (1.39, 1.40):
        start = initial_state(room, x, 2.)
        before = scene.sense(start)
        after_state, outcome = advance(
            room, start, tick=1, dx=.04, dy=0.,
            expected_revision=start.revision,
        )
        assert outcome["blocked"]
        after = scene.sense(after_state)
        model_prior = memory.predict(before, "x+")
        memory.observe(before, "x+", after, outcome["blocked"], model_prior)
        sensors.append(before)
    assert sensors[0]["range_bucket_10cm"] == sensors[1]["range_bucket_10cm"]
    return memory, scene, room, sensors


class EmpiricalMotorAdvisorTests(unittest.TestCase):
    def setUp(self):
        context = tempfile.TemporaryDirectory()
        self.addCleanup(context.cleanup)
        self.db = Path(context.name) / "organism.sqlite3"
        self.other = Path(context.name) / "control.sqlite3"

    def baby(self, db=None, *, action_advice_mode="learned", **kwargs):
        return Aquarium(
            db or self.db, SEED, GRAPH,
            exploration_mode="goal_free", sensory_mode="toy_probe",
            temporal_mode="range_outcome", action_advice_mode=action_advice_mode,
            **kwargs,
        )

    def test_empty_memory_never_renders_preloaded_blocked_hint(self):
        _, scene, room, sensors = actual_blocking_evidence()
        fresh = RangeOutcomeMemory()
        first = advisory(fresh, sensors[0], "learned")
        self.assertEqual(first["schema"], SCHEMA)
        self.assertEqual(first["applied_hints"], [])
        self.assertEqual(first["blocked_hints"], [])
        self.assertTrue(all(row["observations"] == 0 for row in first["candidates"]))
        self.assertEqual(first["min_blocked_samples"], MIN_BLOCKED_SAMPLES)

    def test_two_real_blocked_observations_veto_x_but_not_other_actions(self):
        memory, scene, room, sensors = actual_blocking_evidence()
        control = advisory(memory, sensors[0], "observe")
        learned = advisory(memory, sensors[0], "learned")
        self.assertEqual(control["blocked_hints"], ["x+"])
        self.assertEqual(control["applied_hints"], [])
        self.assertEqual(learned["applied_hints"], ["x+"])
        self.assertEqual(memory.updates, 2)
        self.assertEqual([r for r in learned["candidates"]
                          if r["blocked_supported"]], [{
            "action": "x+", "observations": 2,
            "blocked_samples": 2, "success_samples": 0,
            "blocked_supported": True,
        }])

    def test_concrete_frozen_vs_learned_choice_changes_actual_wall_result(self):
        memory, scene, room, cues = actual_blocking_evidence()
        # Commit a genuine first virtual *rest* to each baseline policy.
        frozen, guided = Explorer(), Explorer()
        cue0 = Proprioception(0, cues[0]["world_revision"], 1.0, 2., False)
        for policy in (frozen, guided):
            policy.observe(cue0, policy.decide(cue0), False)
        before = cues[0]
        cue = Proprioception(1, before["world_revision"], 1.39, 2., False)
        baseline = frozen.decide(cue)
        hints = tuple(advisory(memory, before, "learned")["applied_hints"])
        learned = guided.decide(cue, avoid_actions=hints)
        self.assertEqual(baseline["action"], "x+")
        self.assertEqual(learned["action"], "y+")
        source = initial_state(room, 1.39, 2.)
        frozen_after, fb = advance(
            room, source, tick=1, dx=baseline["dx"], dy=baseline["dy"],
            expected_revision=source.revision,
        )
        guided_after, gb = advance(
            room, source, tick=1, dx=learned["dx"], dy=learned["dy"],
            expected_revision=source.revision,
        )
        self.assertTrue(fb["blocked"])
        self.assertFalse(gb["blocked"])
        self.assertEqual(frozen_after.x, 1.39)
        self.assertAlmostEqual(guided_after.y, 2.04)
        frozen.observe(cue, baseline, fb["blocked"])
        guided.observe(cue, learned, gb["blocked"], avoid_actions=hints)
        self.assertEqual(frozen.updates, guided.updates)
        self.assertNotEqual(frozen.snapshot(), guided.snapshot())

    def test_one_observed_block_and_one_success_do_not_veto(self):
        memory, scene, room, sensors = actual_blocking_evidence()
        open_room = Room(4., 4.)
        state = initial_state(open_room, 1.39, 2.)
        before = scene.sense(state)
        next_world, outcome = advance(
            open_room, state, tick=1, dx=.04, dy=0.,
            expected_revision=state.revision,
        )
        self.assertFalse(outcome["blocked"])
        after = scene.sense(next_world)
        memory.observe(before, "x+", after, False,
                       memory.predict(before, "x+"))
        result = advisory(memory, before, "learned")
        self.assertEqual(result["blocked_hints"], [])
        self.assertEqual(result["applied_hints"], [])
        row = next(r for r in result["candidates"] if r["action"] == "x+")
        self.assertEqual((row["blocked_samples"], row["success_samples"]), (2, 1))

    def test_unsupported_or_new_numeric_bucket_is_not_vetoed(self):
        memory, scene, room, sensors = actual_blocking_evidence()
        novel = scene.sense(initial_state(Room(4., 4.), 1.29, 2.))
        self.assertNotEqual(novel["range_bucket_10cm"],
                            sensors[0]["range_bucket_10cm"])
        self.assertEqual(advisory(memory, novel, "learned")["applied_hints"], [])
        self.assertEqual(advisory(memory, sensors[0], "learned")["applied_hints"],
                         ["x+"])
        self.assertNotIn("private.experiment",
                         json.dumps(advisory(memory, sensors[0], "learned")))
        self.assertNotIn("210", json.dumps(advisory(memory, sensors[0], "learned")))

    def test_adviser_is_read_only_and_snapshot_remains_identical(self):
        memory, scene, room, sensors = actual_blocking_evidence()
        before = memory.snapshot()
        for _ in range(10):
            advisory(memory, sensors[0], "learned")
            advisory(memory, sensors[0], "observe")
        self.assertEqual(memory.snapshot(), before)
        self.assertEqual(RangeOutcomeMemory.from_snapshot(before).snapshot(),
                         before)

    def test_invalid_hints_are_rejected_before_policy_update(self):
        memory, scene, room, sensors = actual_blocking_evidence()
        cue = Proprioception(0, sensors[0]["world_revision"], 1.39, 2., False)
        policy = Explorer()
        for bad in ("x+", ["x+"], ("rest",), ("x+", "x+"),
                    ("teleport",), (42,)):
            with self.subTest(hints=bad), self.assertRaises(DevelopmentError):
                policy.decide(cue, avoid_actions=bad)
        self.assertEqual(policy.updates, 0)
        for wrong in ("disabled", True, None, "", "learned_forged"):
            with self.subTest(mode=wrong), self.assertRaises(ActionAdviceError):
                advisory(memory, sensors[0], wrong)

    def test_new_mode_birth_without_predictions_stays_exploratory(self):
        with self.baby(neural_mode="disabled") as a:
            frame = a.step()
            self.assertEqual(frame["action_advice"]["schema"], SCHEMA)
            self.assertEqual(frame["action_advice"]["mode"], "learned")
            self.assertEqual(frame["action_advice"]["applied_hints"], [])
            self.assertEqual(frame["developmental"]["intent"]["action"],
                             frame["developmental"]["learning"]["action"])
            self.assertEqual(a.store.count(), 2)
            self.assertEqual(a.store.dataset()["projection"]["items"], [])
            self.assertEqual(a.checkpoint()["action_advice_mode"], "learned")
            self.assertEqual(a.temporal_model.updates, 1)
            self.assertIsNone(frame["brain"]["action"])

    def test_two_canonical_arm_modes_share_action_until_evidence_suffices(self):
        with self.baby(action_advice_mode="learned",
                       neural_mode="disabled") as guided, \
             self.baby(self.other, action_advice_mode="observe",
                       neural_mode="disabled") as observation_only:
            a = guided.step()
            b = observation_only.step()
            self.assertEqual(a["movement_request"], b["movement_request"])
            self.assertEqual(a["temporal_range"], b["temporal_range"])
            self.assertEqual(a["developmental"], b["developmental"])
            self.assertEqual(a["action_advice"]["blocked_hints"],
                             b["action_advice"]["blocked_hints"])
            self.assertEqual(a["action_advice"]["applied_hints"], [])
            self.assertEqual(b["action_advice"]["applied_hints"], [])
            self.assertEqual(guided.temporal_model.snapshot(),
                             observation_only.temporal_model.snapshot())
            self.assertNotEqual(guided.manifest, observation_only.manifest)

    def test_replay_resumes_advice_from_pre_action_memory(self):
        with self.baby(neural_mode="disabled") as a, \
             self.baby(self.other, neural_mode="disabled") as control:
            for _ in range(36):
                self.assertEqual(a.step(), control.step())
            saved = a.checkpoint()
            future = control.step()
        with self.baby(neural_mode="disabled") as resumed:
            self.assertEqual(resumed.checkpoint(), saved)
            self.assertEqual(resumed.step(), future)
            self.assertEqual(resumed.store.count(), 38)

    def test_append_failure_never_adopts_actor_or_advice_memory(self):
        with self.baby(neural_mode="disabled") as a:
            old = a.checkpoint()
            with patch.object(a, "_append_record",
                              side_effect=NoosphereError("rejected")):
                with self.assertRaises(NoosphereError):
                    a.step()
            self.assertEqual(a.checkpoint(), old)
            self.assertEqual(a.store.count(), 1)

    def test_commit_before_ram_adoption_recovers_exact_actor(self):
        with self.baby(neural_mode="disabled") as a:
            frame, _, _ = a._calculate(None, None)
            a._append_record("aquarium.frame", "frame-000000000000",
                             frame, 0, a.genome.revision)
            self.assertEqual(a.explorer.updates, 0)
            self.assertEqual(a.temporal_model.updates, 0)
            with self.assertRaises(AquariumError):
                a.checkpoint()
        with self.baby(neural_mode="disabled") as recovered:
            self.assertEqual(recovered.explorer.updates, 1)
            self.assertEqual(recovered.temporal_model.updates, 1)
            self.assertEqual(recovered.tick, 1)

    def test_stale_writer_does_not_adopt_policy_or_export_checkpoint(self):
        with self.baby(neural_mode="disabled") as first, \
             self.baby(neural_mode="disabled") as stale:
            old_explorer = stale.explorer.snapshot()
            old_temporal = stale.temporal_model.snapshot()
            first.step()
            with self.assertRaises(NoosphereError):
                stale.step()
            self.assertEqual(stale.explorer.snapshot(), old_explorer)
            self.assertEqual(stale.temporal_model.snapshot(), old_temporal)
            with self.assertRaises(AquariumError):
                stale.checkpoint()

    def test_incompatible_mode_cannot_reinterpret_journal(self):
        with self.baby(neural_mode="disabled") as a:
            a.step()
        for other_mode in ("observe", "none"):
            with self.subTest(other_mode=other_mode):
                with self.assertRaises(AquariumError):
                    self.baby(neural_mode="disabled",
                              action_advice_mode=other_mode)
        with self.baby(neural_mode="disabled") as same:
            self.assertEqual(same.tick, 1)

    def test_forged_advice_evidence_is_rejected_before_ram_adoption(self):
        with self.baby(neural_mode="disabled") as a:
            payload, _, _ = a._calculate(None, None)
            baseline = a.checkpoint()
            forged = copy.deepcopy(payload)
            forged["action_advice"]["applied_hints"] = ["x+"]
            with self.assertRaises(AquariumError):
                a._adopt_developmental(forged)
            self.assertEqual(a.checkpoint(), baseline)
            self.assertEqual(a.store.count(), 1)

    def test_120_plus_30_cli_reopens_one_exact_journal(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [
            sys.executable, "-m", "mojum.aquarium", "--db", str(self.db),
            "--neural-mode", "disabled", "--exploration-mode", "goal_free",
            "--sensory-mode", "toy_probe", "--temporal-mode", "range_outcome",
            "--action-advice-mode", "learned",
        ]
        first = json.loads(subprocess.check_output(
            [*command, "--ticks", "120"], env=env, cwd=ROOT,
            text=True, timeout=30,
        ))
        second = json.loads(subprocess.check_output(
            [*command, "--ticks", "30"], env=env, cwd=ROOT,
            text=True, timeout=30,
        ))
        self.assertEqual(first["tick"], 120)
        self.assertEqual(second["tick"], 150)
        self.assertEqual(second["stored_events"], 151)
        self.assertEqual(second["developmental_state"]["updates"], 150)
        self.assertEqual(second["temporal_range_state"]["updates"], 150)
        self.assertEqual(second["action_advice_mode"], "learned")

    def test_cli_validates_mode_before_creating_sqlite_then_resumes(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        command = [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.db),
            "--ticks", "2", "--neural-mode", "disabled",
            "--exploration-mode", "goal_free", "--sensory-mode", "toy_probe",
            "--temporal-mode", "range_outcome",
            "--action-advice-mode", "learned",
        ]
        for invalid in (
            [sys.executable, "-m", "mojum.aquarium",
             "--db", str(self.db), "--action-advice-mode", "learned"],
            [sys.executable, "-m", "mojum.aquarium",
             "--db", str(self.db), "--temporal-mode", "range_outcome",
             "--action-advice-mode", "learned"],
        ):
            bad = subprocess.run(invalid, cwd=ROOT, env=env,
                                 text=True, capture_output=True, timeout=30)
            self.assertEqual(bad.returncode, 2)
            self.assertFalse(self.db.exists())
        first = json.loads(subprocess.check_output(
            command, env=env, cwd=ROOT, text=True, timeout=30,
        ))
        next_one = json.loads(subprocess.check_output(
            command, env=env, cwd=ROOT, text=True, timeout=30,
        ))
        self.assertEqual(first["tick"], 2)
        self.assertEqual(next_one["tick"], 4)
        self.assertEqual(next_one["stored_events"], 5)
        self.assertEqual(next_one["action_advice_mode"], "learned")


if __name__ == "__main__":
    unittest.main()
