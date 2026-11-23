"""Standalone regression tests for garment transition replay."""
import unittest

from mojum.garment_transitions import (
    Garment, GarmentTransitionError, Transition, Wardrobe, apply, replay,
)


class GarmentTransitionTests(unittest.TestCase):
    def setUp(self):
        self.shirt = Garment("shirt", "torso", "base")
        self.jacket = Garment("jacket", "torso", "outer")

    def test_roundtrip_and_canonical_order(self):
        events = [
            Transition(1, "equip", self.jacket),
            Transition(2, "equip", self.shirt),
            Transition(3, "remove", self.jacket),
            Transition(4, "remove", self.shirt),
        ]
        self.assertEqual(replay(events), Wardrobe(4, ()))
        self.assertEqual(replay(events[:2]).worn, (self.shirt, self.jacket))

    def test_layer_collision(self):
        first = apply(Wardrobe(), Transition(1, "equip", self.shirt))
        with self.assertRaises(GarmentTransitionError):
            apply(first, Transition(2, "equip", Garment("other", "torso", "base")))

    def test_outer_layer_blocks_inner_removal(self):
        state = replay([Transition(1, "equip", self.shirt),
                        Transition(2, "equip", self.jacket)])
        with self.assertRaises(GarmentTransitionError):
            apply(state, Transition(3, "remove", self.shirt))

    def test_stale_revision_and_duplicate(self):
        state = apply(Wardrobe(), Transition(1, "equip", self.shirt))
        for event in (Transition(1, "remove", self.shirt),
                      Transition(2, "equip", self.shirt),
                      Transition(2, "remove", Garment("shirt", "legs", "base"))):
            with self.subTest(event=event), self.assertRaises(GarmentTransitionError):
                apply(state, event)

    def test_invalid_identifiers_and_types(self):
        for name in ("", "../bad", "bad name", "a" * 97):
            with self.subTest(name=name), self.assertRaises(GarmentTransitionError):
                Garment(name, "torso", "base")
        with self.assertRaises(GarmentTransitionError):
            Garment("ok", "torso", "unknown")
        with self.assertRaises(GarmentTransitionError):
            Transition(True, "equip", self.shirt)
        with self.assertRaises(GarmentTransitionError):
            Wardrobe(True, ())

    def test_replay_rejects_gaps(self):
        with self.assertRaises(GarmentTransitionError):
            replay([Transition(2, "equip", self.shirt)])

    def test_immutable_input(self):
        state = Wardrobe()
        updated = apply(state, Transition(1, "equip", self.shirt))
        self.assertEqual(state, Wardrobe())
        self.assertEqual(updated.revision, 1)


if __name__ == "__main__":
    unittest.main()
