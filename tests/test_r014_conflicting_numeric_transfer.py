"""QA: coarse range/action transfer is ambiguous across mirrored toy scenes.

This deliberately measures contradictory synthetic outcomes. The model may
abstain; an arbitrary lexical winner must never be treated as learned evidence
for motor veto/preference. No private journal is exported from these tests.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.developmental_policy import Proprioception
from mojum.sensory_play import Toy, ToyScene
from mojum.temporal_outcomes import (
    RangeOutcomeMemory, TemporalOutcomeError, SCHEMA,
)
from mojum.world_room import Room, advance, initial_state

ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def scenes():
    # Both unseen toy identities and RGB differ; *only* the numeric 10cm
    # distance/action pair reaches the temporal learner.
    return (
        ToyScene(toys=(Toy("spectator.right", 1.22, 2., .1, (230, 35, 15), .2),)),
        ToyScene(toys=(Toy("spectator.left", .78, 2., .1, (15, 180, 240), .8),)),
    )


def observed_step(scene, room):
    before_state = initial_state(room, 1., 2.)
    before = scene.sense(before_state, occluding_obstacles=room.obstacles)
    after_state, result = advance(
        room, before_state, tick=1, dx=.04, dy=0.,
        expected_revision=before_state.revision,
    )
    after = scene.sense(after_state, occluding_obstacles=room.obstacles)
    return before, after, result


class ConflictingTemporalEvidenceQA(unittest.TestCase):
    def test_mirrored_position_contradicts_identical_numeric_context(self):
        room = Room(4., 4.)
        right, left = scenes()
        first, first_after, actual_right = observed_step(right, room)
        second, second_after, actual_left = observed_step(left, room)
        self.assertEqual(first["range_bucket_10cm"], 2)
        self.assertEqual(second["range_bucket_10cm"], 2)
        self.assertNotEqual(first["reflectance_rgb"],
                            second["reflectance_rgb"])
        self.assertFalse(actual_right["blocked"])
        self.assertFalse(actual_left["blocked"])
        self.assertEqual(first_after["range_bucket_10cm"], 1)
        self.assertEqual(second_after["range_bucket_10cm"], 2)

        learned = RangeOutcomeMemory()
        learned.observe(first, "x+", first_after, False,
                        learned.predict(first, "x+"))
        # A legitimate empirical prediction in the training scene becomes
        # wrong when the same numeric cue is observed with mirrored geometry.
        prior = learned.predict(second, "x+")
        self.assertEqual(prior, {
            "observations": 1, "next_bucket": 1, "blocked": False,
        })
        self.assertNotEqual(prior["next_bucket"],
                            second_after["range_bucket_10cm"])
        learned.observe(second, "x+", second_after, False, prior)
        self.assertEqual(learned.predict(first, "x+"), {
            "observations": 2, "next_bucket": None, "blocked": None,
        })
        self.assertEqual(RangeOutcomeMemory.from_snapshot(
            learned.snapshot()).snapshot(), learned.snapshot())

    def test_heldout_wall_contradicts_predicted_unblocked_motion(self):
        free = Room(4., 4.)
        wall = Room(4., 4., ((1.01, 1.96, .01, .08),))
        right, left = scenes()
        before, after, free_result = observed_step(right, free)
        unseen, blocked_after, blocked_result = observed_step(left, wall)
        self.assertFalse(free_result["blocked"])
        self.assertTrue(blocked_result["blocked"])
        self.assertEqual(blocked_result["position"], [1., 2.])
        self.assertEqual(before["range_bucket_10cm"],
                         unseen["range_bucket_10cm"])
        learned = RangeOutcomeMemory()
        learned.observe(before, "x+", after, False,
                        learned.predict(before, "x+"))
        prediction = learned.predict(unseen, "x+")
        self.assertFalse(prediction["blocked"])
        self.assertEqual(prediction["next_bucket"], 1)
        self.assertEqual(blocked_after["range_bucket_10cm"], 2)
        learned.observe(unseen, "x+", blocked_after, True, prediction)
        # In one scene x+ succeeded; in the other the same numeric cue was
        # blocked. A 1:1 tie must not fabricate safe/unblocked certainty.
        self.assertEqual(learned.predict(unseen, "x+"), {
            "observations": 2, "next_bucket": None, "blocked": None,
        })

    def test_guided_policy_reverts_to_bounded_motor_play_on_tie(self):
        right, left = scenes()
        room = Room(4., 4.)
        a, b, _ = observed_step(right, room)
        c, d, _ = observed_step(left, room)
        model = RangeOutcomeMemory()
        model.observe(a, "x+", b, False, model.predict(a, "x+"))
        with tempfile.TemporaryDirectory() as directory:
            with Aquarium(
                    Path(directory) / "counterfactual.sqlite3", SEED, GRAPH,
                    room=room, sensory_scene=right,
                    neural_mode="disabled", exploration_mode="goal_free",
                    sensory_mode="toy_probe",
                    temporal_mode="range_guided") as organism:
                # QA-only injected history to isolate decision semantics.
                # No misleading claim that it was committed to the journal.
                organism.temporal_model = model
                cue = Proprioception(
                    0, organism.world.revision, organism.world.x,
                    organism.world.y, False,
                )
                baseline = organism.explorer.decide(cue)
                self.assertEqual(baseline["action"], "rest")
                _, veto, prefer = organism._motor_guidance()
                self.assertEqual(veto, ())
                self.assertIn("x+", prefer)
                self.assertEqual(organism.explorer.decide(
                    cue, avoid=veto, prefer=prefer)["action"], "x+")
                # Add the opposed measured outcome. Prediction abstains, so
                # known contradictory evidence can no longer force x+.
                model.observe(c, "x+", d, False, model.predict(c, "x+"))
                guide, veto, prefer = organism._motor_guidance()
                self.assertEqual(guide["predictions"]["x+"]["observations"], 2)
                self.assertIsNone(guide["predictions"]["x+"]["blocked"])
                self.assertEqual((veto, prefer), ((), ()))
                self.assertEqual(organism.explorer.decide(
                    cue, avoid=veto, prefer=prefer), baseline)
                self.assertEqual(organism.store.count(), 1)

    def test_ambiguous_prediction_is_not_reported_as_known_forecast(self):
        room = Room(4., 4.)
        right, left = scenes()
        first, first_after, _ = observed_step(right, room)
        second, second_after, _ = observed_step(left, room)
        model = RangeOutcomeMemory()
        model.observe(first, "x+", first_after, False,
                      model.predict(first, "x+"))
        model.observe(second, "x+", second_after, False,
                      model.predict(second, "x+"))
        self.assertIsNone(model.predict(first, "x+")["blocked"])
        observed = model.observe(first, "x+", first_after, False,
                                 model.predict(first, "x+"))
        self.assertFalse(observed["prediction_was_known"])
        self.assertEqual(model.predict(first, "x+")["observations"], 3)

    def test_old_prediction_version_rejected_not_silently_replayed(self):
        model = RangeOutcomeMemory()
        self.assertEqual(SCHEMA, "mojum.temporal.range-outcome/2")
        old = copy.deepcopy(model.snapshot())
        old["schema"] = "mojum.temporal.range-outcome/1"
        with self.assertRaises(TemporalOutcomeError):
            RangeOutcomeMemory.from_snapshot(old)


if __name__ == "__main__":
    unittest.main()
