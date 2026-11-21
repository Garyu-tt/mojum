import unittest

from mojum.world_room import Room, advance, initial_state


def _step(room, state, dx, dy):
    return advance(room, state, tick=state.tick + 1, dx=dx, dy=dy,
                   expected_revision=state.revision)


class BoundaryContactTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(10.0, 10.0, ((4.0, 4.0, 2.0, 2.0),))

    def test_grazing_obstacle_edge_is_blocked(self):
        s = initial_state(self.room, 3.0, 4.0)
        s2, ev = _step(self.room, s, 3.0, 0.0)  # runs along x=4..6 at y=4 (bottom edge)
        self.assertTrue(ev["blocked"])
        self.assertEqual((s2.x, s2.y), (3.0, 4.0))

    def test_corner_touch_is_blocked(self):
        s = initial_state(self.room, 3.0, 3.0)
        _, ev = _step(self.room, s, 1.0, 1.0)  # ends exactly on the corner
        self.assertTrue(ev["blocked"])

    def test_diagonal_corner_graze_is_blocked(self):
        s = initial_state(self.room, 3.0, 5.0)
        _, ev = _step(self.room, s, 1.0, -1.0)  # ends on corner (4,4)
        self.assertTrue(ev["blocked"])

    def test_clear_parallel_move_is_not_blocked(self):
        s = initial_state(self.room, 3.0, 3.9)
        s2, ev = _step(self.room, s, 3.0, 0.0)
        self.assertFalse(ev["blocked"])
        self.assertEqual((s2.x, s2.y), (6.0, 3.9))

    def test_outer_wall_is_reachable_but_not_crossable(self):
        s = initial_state(self.room, 9.0, 1.0)
        s2, ev = _step(self.room, s, 1.0, 0.0)
        self.assertFalse(ev["blocked"])
        self.assertEqual(s2.x, 10.0)
        _, ev2 = _step(self.room, s2, 0.5, 0.0)
        self.assertTrue(ev2["blocked"])


if __name__ == "__main__":
    unittest.main()
