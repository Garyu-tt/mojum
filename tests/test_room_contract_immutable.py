"""QA for immutable CPU point-room geometry; no MuJoCo or real-body claims."""

import unittest

from mojum.world_room import Room, advance, initial_state


class ImmutableRoomContractTests(unittest.TestCase):
    def test_nested_caller_lists_cannot_change_live_room_or_revision(self):
        rectangles = [[2, 1, 0.1, 2]]
        room = Room(4, 4.0, rectangles)
        state = initial_state(room, 1, 2)
        before = state.revision
        rectangles[0][0] = 3
        rectangles.append([0, 0, 0.1, 0.1])
        self.assertEqual(room.obstacles, ((2, 1, 0.1, 2),))
        self.assertEqual(type(room.width), int)
        self.assertEqual(type(room.height), float)
        self.assertEqual(type(room.obstacles[0][0]), int)
        self.assertEqual(initial_state(room, 1, 2).revision, before)
        moved, observation = advance(
            room, state, tick=1, dx=2, dy=0, expected_revision=before)
        self.assertTrue(observation["blocked"])
        self.assertEqual((moved.x, moved.y), (1, 2))

    def test_caller_cannot_mutate_nested_tuple_after_construction(self):
        room = Room(4, 4, [[2, 1, 0.1, 2]])
        with self.assertRaises(TypeError):
            room.obstacles[0][0] = 3

    def test_invalid_dimension_types_and_gigantic_integers_are_rejected(self):
        for width in (True, False, "4", None, float("nan"),
                      float("inf"), 10**5000):
            with self.subTest(width=str(width)[:24]):
                with self.assertRaises(ValueError):
                    Room(width, 4)

    def test_malformed_obstacles_fail_as_geometry_errors(self):
        invalid = [
            None, "wall", [[2, 1, 0.1]], [[2, 1, 0.1, 2, 4]],
            [[True, 1, 0.1, 2]], [[2, "1", 0.1, 2]],
            [[2, 1, 10**5000, 2]], [[float("nan"), 1, 0.1, 2]],
            [[[2], 1, 0.1, 2]],
        ]
        for obstacles in invalid:
            with self.subTest(obstacles=str(obstacles)[:50]):
                with self.assertRaises(ValueError):
                    Room(4, 4, obstacles)

    def test_unbounded_obstacle_count_is_rejected(self):
        with self.assertRaises(ValueError):
            Room(4, 4, [(1, 1, 0.1, 0.1)] * 4097)

    def test_invalid_positions_and_displacements_fail_closed(self):
        room = Room(4, 4)
        state = initial_state(room, 1, 1)
        for coordinate in (True, "1", 10**5000):
            with self.subTest(coordinate=str(coordinate)[:24]):
                with self.assertRaises(ValueError):
                    initial_state(room, coordinate, 1)
                with self.assertRaises(ValueError):
                    advance(room, state, tick=1, dx=coordinate, dy=0,
                            expected_revision=state.revision)
        for tick in (True, 1.0, "1"):
            with self.subTest(tick=tick):
                with self.assertRaises(ValueError):
                    advance(room, state, tick=tick, dx=0, dy=0,
                            expected_revision=state.revision)
        self.assertEqual(initial_state(room, 1, 1), state)


if __name__ == "__main__":
    unittest.main()
