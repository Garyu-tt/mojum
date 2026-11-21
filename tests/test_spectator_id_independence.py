"""Adversarial R0.13 QA: spectator labels are *not* a sensory tie-breaker.

Two equidistant toys can have different RGB and hidden softness.
Renaming their spectator-only IDs must not change the learner's numeric
observation or the instrument's read-only measured contact.
"""
import json
from pathlib import Path
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.sensory_play import CuriosityError, Toy, ToyScene
from mojum.world_room import Room, initial_state


ROOT = Path(__file__).resolve().parent.parent
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def labeled_scene(swap_names=False, reverse_order=False):
    # Both centers are *exactly* 0.25m away from (2,2), using binary-exact
    # coordinates. Physically different toys have equal near-field distance.
    left_id = "spectator.zeta" if swap_names else "spectator.alpha"
    right_id = "spectator.alpha" if swap_names else "spectator.zeta"
    left = Toy(left_id, 1.75, 2.0, .24, (220, 80, 30), .10)
    right = Toy(right_id, 2.25, 2.0, .24, (20, 130, 220), .90)
    ordered = (right, left) if reverse_order else (left, right)
    return ToyScene(width=4.0, height=4.0, toys=ordered)


class SpectatorIdentityIndependenceQA(unittest.TestCase):
    def test_identical_observable_toys_different_hidden_response_fail_closed(self):
        # If all public tie-break fields are identical, Python min() falls
        # back to tuple iteration order and changes an otherwise hidden probe.
        # The scene must reject that ambiguity before creating a journal.
        left = Toy("spectator.alpha", 1.75, 2.0, .24, (220, 80, 30), .10)
        right = Toy("spectator.zeta", 1.75, 2.0, .24, (220, 80, 30), .90)
        for toys in ((left, right), (right, left)):
            with self.subTest(names=[t.id for t in toys]):
                with self.assertRaisesRegex(CuriosityError, "ambiguous observationally identical"):
                    ToyScene(width=4.0, height=4.0, toys=toys)

    def test_numeric_perception_independent_of_spectator_renaming(self):
        room = Room(4.0, 4.0)
        start = initial_state(room, 2.0, 2.0)
        baseline = labeled_scene().sense(start)
        self.assertEqual(baseline["reflectance_rgb"], [220, 80, 30])
        self.assertEqual(baseline["range_bucket_10cm"], 2)
        for scene in (labeled_scene(swap_names=True),
                      labeled_scene(reverse_order=True),
                      labeled_scene(swap_names=True, reverse_order=True)):
            with self.subTest(labels=scene.manifest()["toys"]):
                self.assertEqual(scene.sense(start), baseline)
                self.assertNotIn("spectator", str(scene.sense(start)))

    def test_measured_contact_independent_of_spectator_renaming(self):
        state = initial_state(Room(4.0, 4.0), 2.0, 2.0)
        original = labeled_scene()
        measured = original.probe(state, original.sense(state))
        self.assertTrue(measured["contact"])
        self.assertEqual(measured["measured_deflection"], .10)
        for scene in (labeled_scene(swap_names=True),
                      labeled_scene(reverse_order=True),
                      labeled_scene(swap_names=True, reverse_order=True)):
            self.assertEqual(scene.probe(state, scene.sense(state)), measured)

    def test_canonical_aquarium_learns_same_evidence_despite_label_change(self):
        with tempfile.TemporaryDirectory() as temp:
            a_path = Path(temp) / "one.sqlite3"
            b_path = Path(temp) / "two.sqlite3"
            with Aquarium(a_path, SEED, GRAPH, room=Room(4, 4),
                          start=(2.0, 2.0), exploration_mode="goal_free",
                          sensory_mode="toy_probe", sensory_scene=labeled_scene(),
                          neural_mode="disabled") as a, Aquarium(
                          b_path, SEED, GRAPH, room=Room(4, 4),
                          start=(2.0, 2.0), exploration_mode="goal_free",
                          sensory_mode="toy_probe",
                          sensory_scene=labeled_scene(swap_names=True),
                          neural_mode="disabled") as b:
                self.assertNotEqual(a.manifest["sensory_scene"],
                                    b.manifest["sensory_scene"])
                for _ in range(8):
                    frame_a, frame_b = a.step(), b.step()
                    self.assertEqual(frame_a, frame_b)
                    self.assertNotIn("spectator.", json.dumps(frame_a))
                self.assertEqual(a.sensory_bridge.model.snapshot(),
                                 b.sensory_bridge.model.snapshot())
                self.assertEqual(a.explorer.snapshot(), b.explorer.snapshot())
                self.assertEqual(a.store.count(), b.store.count())
                # Private experiment manifests differ (correct provenance),
                # but the agent's experience remains causally identical.
                self.assertNotEqual(a.store.head(), b.store.head())


if __name__ == "__main__":
    unittest.main()
