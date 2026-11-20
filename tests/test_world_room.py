import unittest
from mojum.world_room import Room, initial_state, advance

class RoomFixtureTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, .1, 2),))
        self.start = initial_state(self.room, 1, 2)

    def test_replay(self):
        a = advance(self.room, self.start, tick=1, dx=.5, dy=0, expected_revision=self.start.revision)
        b = advance(self.room, self.start, tick=1, dx=.5, dy=0, expected_revision=self.start.revision)
        self.assertEqual(a, b)
        self.assertFalse(a[1]["physical_contact"])

    def test_obstacle_tunneling(self):
        s, obs = advance(self.room, self.start, tick=1, dx=2, dy=0, expected_revision=self.start.revision)
        self.assertTrue(obs["blocked"])
        self.assertEqual((s.x, s.y), (1, 2))

    def test_wall(self):
        _, obs = advance(self.room, self.start, tick=1, dx=-2, dy=0, expected_revision=self.start.revision)
        self.assertTrue(obs["blocked"])

    def test_cas(self):
        with self.assertRaises(ValueError):
            advance(self.room, self.start, tick=1, dx=0, dy=0, expected_revision="wrong")

    def test_tick(self):
        with self.assertRaises(ValueError):
            advance(self.room, self.start, tick=2, dx=0, dy=0, expected_revision=self.start.revision)

    def test_bad_geometry(self):
        with self.assertRaises(ValueError):
            Room(2, 2, ((1, 1, 2, 1),))

    def test_bad_position(self):
        with self.assertRaises(ValueError):
            initial_state(self.room, 2, 2)

    def test_nonfinite(self):
        with self.assertRaises(ValueError):
            advance(self.room, self.start, tick=1, dx=float("nan"), dy=0, expected_revision=self.start.revision)


class ExactSweptCollisionQA(unittest.TestCase):
    def test_one_millimeter_wall_not_tunneled(self):
        room = Room(4, 4, ((2.004, 1, .001, 2),))
        state = initial_state(room, 1, 2)
        next_state, observation = advance(
            room, state, tick=1, dx=2, dy=0,
            expected_revision=state.revision)
        self.assertTrue(observation["blocked"])
        self.assertEqual((next_state.x, next_state.y), (1, 2))

    def test_diagonal_tunnel_through_tiny_rectangle(self):
        room = Room(4, 4, ((2.004, 2.004, .001, .001),))
        state = initial_state(room, 1, 1)
        next_state, observation = advance(
            room, state, tick=1, dx=2, dy=2,
            expected_revision=state.revision)
        self.assertTrue(observation["blocked"])
        self.assertEqual((next_state.x, next_state.y), (1, 1))

    def test_tangent_boundary_contact_is_blocked(self):
        room = Room(4, 4, ((2, 1, .1, 2),))
        state = initial_state(room, 1, 1)
        _, observation = advance(
            room, state, tick=1, dx=2, dy=0,
            expected_revision=state.revision)
        self.assertTrue(observation["blocked"])

    def test_near_miss_does_not_block(self):
        room = Room(4, 4, ((2.004, 1, .001, 2),))
        state = initial_state(room, 1, .99)
        next_state, observation = advance(
            room, state, tick=1, dx=2, dy=0,
            expected_revision=state.revision)
        self.assertFalse(observation["blocked"])
        self.assertEqual((next_state.x, next_state.y), (3, .99))

    def test_zero_motion_advances_tick(self):
        room = Room(4, 4)
        state = initial_state(room, 1, 1)
        next_state, observation = advance(
            room, state, tick=1, dx=0, dy=0,
            expected_revision=state.revision)
        self.assertFalse(observation["blocked"])
        self.assertEqual(next_state.tick, 1)

    def test_huge_finite_displacement_rejected_with_valueerror(self):
        room = Room(4, 4)
        state = initial_state(room, 1, 1)
        with self.assertRaisesRegex(ValueError, "budget"):
            advance(room, state, tick=1, dx=1e308, dy=1e308,
                    expected_revision=state.revision)

if __name__ == "__main__":
    unittest.main()

