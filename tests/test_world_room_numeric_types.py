"""Fail-closed input tests for the synthetic point-room wire and time contract."""
import unittest

from mojum.world_room import Room, RoomState, advance, initial_state


class RoomNumericTypesTests(unittest.TestCase):
    def test_room_dimensions_must_be_finite_numeric_scalars(self):
        for bad in (True, False, '4', None, [], float('nan'), float('inf'), 10 ** 500):
            with self.subTest(bad_type=type(bad).__name__, bad=str(bad)[:20]):
                with self.assertRaises(ValueError):
                    Room(bad, 4)

    def test_obstacle_shape_and_coordinates_are_checked(self):
        bad_obstacles = (
            None, 'not a list', (None,), ((1, 2, 3),),
            ((1, 1, True, 1),), ((1, '1', 1, 1),),
            ((1, 1, 10 ** 500, 1),), ((1, 1, float('nan'), 1),),
            ((1, 1, 1, 1),) * 4097,
        )
        for bad in bad_obstacles:
            with self.subTest(kind=type(bad).__name__, length=len(bad) if hasattr(bad, '__len__') else 0):
                with self.assertRaises(ValueError):
                    Room(4, 4, bad)

    def test_initial_position_must_be_finite_numeric_scalar(self):
        room = Room(4, 4)
        for bad in (True, False, '1', None, [], float('inf'), float('nan'), 10 ** 500):
            with self.subTest(kind=type(bad).__name__, bad=str(bad)[:20]):
                with self.assertRaises(ValueError):
                    initial_state(room, bad, 1)

    def test_tick_and_movement_do_not_accept_bool_or_float_clock(self):
        room = Room(4, 4)
        start = initial_state(room, 1, 2)
        for tick in (True, 1.0, '1', None):
            with self.subTest(tick=tick):
                with self.assertRaises(ValueError):
                    advance(room, start, tick=tick, dx=0.25, dy=0,
                            expected_revision=start.revision)
        for movement in (True, False, '1', None, [], 10 ** 500):
            with self.subTest(kind=type(movement).__name__):
                with self.assertRaises(ValueError):
                    advance(room, start, tick=1, dx=movement, dy=0,
                            expected_revision=start.revision)

    def test_forged_invalid_state_and_room_refuse_cleanly(self):
        room = Room(4, 4)
        start = initial_state(room, 1, 2)
        for bad_state in (None, RoomState(True, 1, 2, start.revision),
                          RoomState(0, True, 2, start.revision)):
            with self.subTest(state=bad_state):
                with self.assertRaises(ValueError):
                    advance(room, bad_state, tick=1, dx=0, dy=0,
                            expected_revision=start.revision)
        with self.assertRaises(ValueError):
            advance(None, start, tick=1, dx=0, dy=0,
                    expected_revision=start.revision)

    def test_normal_tick_and_collision_behavior_unchanged(self):
        room = Room(4, 4, ((2, 1, .1, 2),))
        start = initial_state(room, 1, 2)
        moved, observation = advance(room, start, tick=1, dx=.25, dy=0,
                                     expected_revision=start.revision)
        self.assertEqual((moved.x, moved.y, moved.tick), (1.25, 2, 1))
        self.assertFalse(observation['blocked'])
        blocked, observation = advance(room, moved, tick=2, dx=2, dy=0,
                                        expected_revision=moved.revision)
        self.assertEqual((blocked.x, blocked.y), (1.25, 2))
        self.assertTrue(observation['blocked'])
        self.assertFalse(observation['physical_contact'])


if __name__ == '__main__':
    unittest.main()
