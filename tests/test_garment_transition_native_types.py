"""Adversarial native-type and mutation regressions for garment transition replay."""
import unittest

from mojum.garment_transitions import (
    Garment, GarmentTransitionError, Transition, Wardrobe, apply, replay,
)


class NativeTypeBoundaryTests(unittest.TestCase):
    def test_id_str_subclass_misreports_iteration(self):
        class ForgedId(str):
            def __iter__(self):
                return iter("safe")

        with self.assertRaises(GarmentTransitionError):
            Garment(ForgedId("../unsafe"), "torso", "base")

    def test_native_str_fields_only(self):
        class StringSubclass(str):
            pass

        with self.assertRaises(GarmentTransitionError):
            Garment("item", StringSubclass("torso"), "base")
        with self.assertRaises(GarmentTransitionError):
            Garment("item", "torso", StringSubclass("base"))
        with self.assertRaises(GarmentTransitionError):
            Transition(1, StringSubclass("equip"), Garment("item", "torso", "base"))

    def test_action_equality_spoof(self):
        class ForgedAction(str):
            def __eq__(self, other):
                return other == "equip"

        with self.assertRaises(GarmentTransitionError):
            Transition(1, ForgedAction("remove"), Garment("item", "torso", "base"))

    def test_garment_metadata_equality_spoof(self):
        class ForgedGarment(Garment):
            def __eq__(self, other):
                return True

        with self.assertRaises(GarmentTransitionError):
            forged = ForgedGarment("item", "torso", "outer")
            state = Wardrobe(1, (Garment("item", "torso", "base"),))
            apply(state, Transition(2, "remove", forged))

    def test_immutable_container_is_native_tuple(self):
        class SneakyTuple(tuple):
            pass

        with self.assertRaises(GarmentTransitionError):
            Wardrobe(0, SneakyTuple())

    def test_mutated_action_checked_at_application(self):
        item = Garment("item", "torso", "base")
        existing = apply(Wardrobe(), Transition(1, "equip", item))
        forged = Transition(2, "remove", item)
        object.__setattr__(forged, "action", "delete")

        with self.assertRaises(GarmentTransitionError):
            apply(existing, forged)
        self.assertEqual(existing.revision, 1)

    def test_mutated_state_checked_before_sort(self):
        item = Garment("item", "torso", "base")
        existing = apply(Wardrobe(), Transition(1, "equip", item))
        object.__setattr__(item, "layer", "not_a_layer")

        with self.assertRaises(GarmentTransitionError):
            apply(existing, Transition(2, "remove", Garment("item", "torso", "base")))

    def test_good_round_trip(self):
        base = Garment("shirt", "torso", "base")
        outer = Garment("jacket", "torso", "outer")
        self.assertEqual(
            replay([
                Transition(1, "equip", outer),
                Transition(2, "equip", base),
                Transition(3, "remove", outer),
                Transition(4, "remove", base),
            ]),
            Wardrobe(4, ()),
        )


if __name__ == "__main__":
    unittest.main()
