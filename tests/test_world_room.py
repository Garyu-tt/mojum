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

if __name__ == "__main__":
    unittest.main()
