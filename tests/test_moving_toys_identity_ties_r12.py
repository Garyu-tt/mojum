"""Adversarial QA: moving-toy sensing must ignore spectator-only identity."""

import unittest

from mojum.moving_toys import MovingToyScene
from mojum.sensory_play import CuriosityError, Toy
from mojum.world_room import advance, initial_state


class MovingToyIdentityTieTests(unittest.TestCase):
    @staticmethod
    def state_at(scene, tick):
        state = initial_state(scene.room, 2.0, 2.0)
        for step in range(1, tick + 1):
            state, _ = advance(
                scene.room, state, tick=step, dx=0, dy=0,
                expected_revision=state.revision,
            )
        return state

    def test_equal_distance_is_independent_of_name_and_tuple_order(self):
        left = Toy("spectator.zeta", 1.75, 2.0, .24, (220, 80, 30), .10)
        right = Toy("spectator.alpha", 2.25, 2.0, .24, (20, 130, 220), .90)
        renamed_left = Toy("spectator.alpha", 1.75, 2.0, .24, (220, 80, 30), .10)
        renamed_right = Toy("spectator.zeta", 2.25, 2.0, .24, (20, 130, 220), .90)
        scenes = (
            MovingToyScene(toys=(left, right),
                           velocities=((.01, 0.0), (-.01, 0.0))),
            MovingToyScene(toys=(right, left),
                           velocities=((- .01, 0.0), (.01, 0.0))),
            MovingToyScene(toys=(renamed_left, renamed_right),
                           velocities=((.01, 0.0), (-.01, 0.0))),
        )
        for tick in (0, 1, 3):
            state = self.state_at(scenes[0], tick)
            expected_cue = scenes[0].sense(state)
            expected_probe = scenes[0].probe(state, expected_cue)
            self.assertEqual(expected_cue["reflectance_rgb"], [220, 80, 30])
            for scene in scenes[1:]:
                with self.subTest(tick=tick, spectator=scene.manifest()):
                    cue = scene.sense(state)
                    self.assertEqual(cue, expected_cue)
                    self.assertEqual(scene.probe(state, cue), expected_probe)
                    self.assertNotIn("spectator.", str(cue))

    def test_identical_observable_toys_with_different_hidden_response_fail_closed(self):
        a = Toy("spectator.alpha", 1.75, 2.0, .24, (220, 80, 30), .10)
        b = Toy("spectator.zeta", 1.75, 2.0, .24, (220, 80, 30), .90)
        for toys in ((a, b), (b, a)):
            with self.subTest(order=tuple(toy.id for toy in toys)):
                scene = MovingToyScene(toys=toys,
                                       velocities=((.01, 0.0), (-.01, 0.0)))
                state = self.state_at(scene, 0)
                with self.assertRaisesRegex(
                    CuriosityError, "ambiguous observationally identical moving toys"
                ):
                    scene.sense(state)


if __name__ == "__main__":
    unittest.main()
