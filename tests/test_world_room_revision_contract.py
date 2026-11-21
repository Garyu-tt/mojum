"""Regression tests for the point-room revision/CAS contract.

The revision hash is computed from the exact JSON spelling of the numbers
(``1`` and ``1.0`` are different wire values), because journal replay depends
on that. A blocked tick must keep the position but still advance tick and
revision, and every revision must chain into the next compare-and-set.
"""
import unittest

from mojum.world_room import Room, advance, initial_state


class RevisionSpellingContract(unittest.TestCase):
    def test_int_and_float_spellings_have_distinct_revisions(self):
        room = Room(4, 4)
        as_int = initial_state(room, 1, 2)
        as_float = initial_state(room, 1.0, 2.0)
        self.assertNotEqual(as_int.revision, as_float.revision)

    def test_same_spelling_gives_same_revision(self):
        room = Room(4, 4, ((2, 1, .1, 2),))
        self.assertEqual(initial_state(room, 1, 2).revision,
                         initial_state(room, 1, 2).revision)

    def test_room_geometry_spelling_is_part_of_revision(self):
        a = initial_state(Room(4, 4), 1, 1)
        b = initial_state(Room(4.0, 4.0), 1, 1)
        self.assertNotEqual(a.revision, b.revision)


class BlockedTickContract(unittest.TestCase):
    def setUp(self):
        self.room = Room(4, 4, ((2, 1, .1, 2),))
        self.start = initial_state(self.room, 1, 2)

    def test_blocked_tick_keeps_position_but_advances_tick_and_revision(self):
        state, obs = advance(self.room, self.start, tick=1, dx=2, dy=0,
                             expected_revision=self.start.revision)
        self.assertTrue(obs["blocked"])
        self.assertEqual((state.x, state.y), (self.start.x, self.start.y))
        self.assertEqual(state.tick, 1)
        self.assertNotEqual(state.revision, self.start.revision)
        self.assertEqual(obs["revision"], state.revision)
        self.assertEqual(obs["position"], [1, 2])

    def test_consecutive_blocked_ticks_get_distinct_revisions(self):
        s1, _ = advance(self.room, self.start, tick=1, dx=2, dy=0,
                        expected_revision=self.start.revision)
        s2, obs = advance(self.room, s1, tick=2, dx=2, dy=0,
                          expected_revision=s1.revision)
        self.assertTrue(obs["blocked"])
        self.assertEqual((s1.x, s1.y), (s2.x, s2.y))
        self.assertNotEqual(s1.revision, s2.revision)

    def test_stale_revision_is_rejected_after_blocked_tick(self):
        s1, _ = advance(self.room, self.start, tick=1, dx=2, dy=0,
                        expected_revision=self.start.revision)
        with self.assertRaisesRegex(ValueError, "revision"):
            advance(self.room, s1, tick=2, dx=0, dy=0,
                    expected_revision=self.start.revision)

    def test_forged_state_revision_is_rejected(self):
        s1, _ = advance(self.room, self.start, tick=1, dx=.5, dy=0,
                        expected_revision=self.start.revision)
        forged = type(s1)(s1.tick, s1.x, s1.y, "0" * 64)
        with self.assertRaisesRegex(ValueError, "revision"):
            advance(self.room, forged, tick=2, dx=0, dy=0,
                    expected_revision=forged.revision)

    def test_observation_marks_fixture_not_physical(self):
        _, obs = advance(self.room, self.start, tick=1, dx=2, dy=0,
                         expected_revision=self.start.revision)
        self.assertFalse(obs["physical_contact"])
        self.assertEqual(obs["provenance"], "deterministic-point-room-fixture/v1")


if __name__ == "__main__":
    unittest.main()
