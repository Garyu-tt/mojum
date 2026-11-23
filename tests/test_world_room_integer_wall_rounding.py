"""QA regression: non-representable integer walls must not hide outward steps."""

from fractions import Fraction
import unittest

from mojum.world_room import Room, advance, initial_state


class IntegerWallRoundingQATests(unittest.TestCase):
    def _step(self, room, x, y, *, dx=0.0, dy=0.0):
        start = initial_state(room, x, y)
        end, frame = advance(
            room, start, tick=1, dx=dx, dy=dy,
            expected_revision=start.revision,
        )
        self.assertEqual(end.tick, 1)
        self.assertEqual(frame["revision"], end.revision)
        self.assertNotEqual(end.revision, start.revision)
        return start, end, frame

    def test_right_integer_wall_rounds_below_actual_wall(self):
        edge = 2**60
        upper = edge + 17
        rounded = edge + 20.0
        self.assertLess(rounded, upper)
        self.assertNotEqual(rounded, upper)
        self.assertGreater(Fraction(edge) + Fraction(20.0), upper)
        initial, final, frame = self._step(
            Room(upper, 1.0), edge, 0.5, dx=20.0,
        )
        self.assertTrue(frame["blocked"])
        self.assertEqual((final.x, final.y), (initial.x, initial.y))

    def test_top_integer_wall_rounds_below_actual_wall(self):
        edge = 2**60
        upper = edge + 17
        initial, final, frame = self._step(
            Room(1.0, upper), 0.5, edge, dy=20.0,
        )
        self.assertTrue(frame["blocked"])
        self.assertEqual((final.x, final.y), (initial.x, initial.y))

    def test_integer_wall_exact_landing_still_allowed(self):
        edge = 2**60
        room = Room(edge + 17, 1.0)
        initial, final, frame = self._step(room, edge, 0.5, dx=17.0)
        self.assertFalse(frame["blocked"])
        self.assertEqual((final.x, final.y), (initial.x, initial.y))

    def test_integer_wall_understep_still_allowed(self):
        edge = 2**60
        room = Room(edge + 17, 1.0)
        initial, final, frame = self._step(room, edge, 0.5, dx=10.0)
        self.assertFalse(frame["blocked"])
        self.assertEqual((final.x, final.y), (initial.x, initial.y))

    def test_larger_integer_wall_is_guarded(self):
        edge = 2**100
        # Keep the requested displacement within the fixture's 1000 m cap
        # while still crossing the non-representable integer wall exactly.
        room = Room(edge + 512, 1.0)
        initial, final, frame = self._step(room, edge, 0.5, dx=1000.0)
        self.assertTrue(frame["blocked"])
        self.assertEqual((final.x, final.y), (initial.x, initial.y))


if __name__ == "__main__":
    unittest.main()
