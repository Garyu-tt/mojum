"""Adversarial R0.13 QA: spectator labels are *not* a sensory tie-breaker.

Two equidistant toys can have different RGB and hidden softness.
Renaming their spectator-only IDs must not change the learner's numeric
observation or the instrument's read-only measured contact.
"""
import json
import unittest
from pathlib import Path

from mojum.sensory_curiosity_lab import CuriosityError, Toy, ToyScene
from mojum.world_room import Room, initial_state



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


if __name__ == "__main__":
    unittest.main()
