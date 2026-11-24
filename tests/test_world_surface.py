import unittest

from mojum.world_surface import (
    SCHEMA, SurfaceMap, SurfacePatch, observe_surface, surface_map_digest,
)

REV = "a" * 64


def patch(pid="tile", material="wood", x=0, y=0, w=1000, h=1000,
          friction=500, restitution=0, priority=0):
    return SurfacePatch(pid, material, x, y, w, h, friction, restitution, priority)


class SurfaceMapTests(unittest.TestCase):
    def make_map(self, patches=()):
        return SurfaceMap(REV, 5000, 4000, tuple(patches))

    def test_observation_inside_patch(self):
        result = observe_surface(self.make_map([patch(friction=720, restitution=80)]),
                                 x_mm=10, y_mm=20, tick=3,
                                 expected_room_revision=REV)
        self.assertEqual(result["material_id"], "wood")
        self.assertEqual(result["friction_milli"], 720)
        self.assertEqual(result["tick"], 3)
        self.assertTrue(result["observation_only"])
        self.assertFalse(result["physical_contact"])

    def test_default_outside_patches(self):
        result = observe_surface(self.make_map([patch()]), x_mm=2000, y_mm=2000,
                                 tick=0, expected_room_revision=REV)
        self.assertIsNone(result["patch_id"])
        self.assertEqual(result["material_id"], "default")
        self.assertEqual(result["friction_milli"], 500)

    def test_half_open_edges(self):
        m = self.make_map([patch()])
        self.assertEqual(observe_surface(m, x_mm=999, y_mm=999, tick=0,
                         expected_room_revision=REV)["patch_id"], "tile")
        self.assertIsNone(observe_surface(m, x_mm=1000, y_mm=999, tick=0,
                         expected_room_revision=REV)["patch_id"])

    def test_room_upper_boundary_is_exclusive(self):
        for x_mm, y_mm in ((5000, 1), (1, 4000)):
            with self.subTest(x_mm=x_mm, y_mm=y_mm):
                with self.assertRaisesRegex(ValueError, "outside room"):
                    observe_surface(self.make_map(), x_mm=x_mm, y_mm=y_mm,
                                    tick=0, expected_room_revision=REV)

    def test_priority_then_identifier_tie_break(self):
        m = self.make_map([
            patch("z", "stone", priority=4),
            patch("b", "rubber", priority=4),
            patch("a", "felt", priority=3),
        ])
        out = observe_surface(m, x_mm=1, y_mm=1, tick=1,
                              expected_room_revision=REV)
        self.assertEqual(out["patch_id"], "b")
        self.assertEqual(out["material_id"], "rubber")

    def test_digest_independent_of_patch_input_order(self):
        a, b = patch("a"), patch("b", x=1000)
        self.assertEqual(surface_map_digest(self.make_map([a, b])),
                         surface_map_digest(self.make_map([b, a])))

    def test_stale_revision_rejected(self):
        with self.assertRaises(ValueError):
            observe_surface(self.make_map(), x_mm=1, y_mm=1, tick=0,
                            expected_room_revision="b" * 64)

    def test_bool_and_float_rejected_for_integer_fields(self):
        for kwargs in ({"x_mm": True}, {"tick": False}, {"tick": 1.0}):
            args = dict(x_mm=1, y_mm=1, tick=0, expected_room_revision=REV)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                observe_surface(self.make_map(), **args)

    def test_out_of_room_and_invalid_geometry_rejected(self):
        with self.assertRaises(ValueError):
            observe_surface(self.make_map(), x_mm=5001, y_mm=1, tick=0,
                            expected_room_revision=REV)
        with self.assertRaises(ValueError):
            patch(w=True)
        with self.assertRaises(ValueError):
            self.make_map([patch(x=4500, w=1000)])

    def test_duplicate_ids_and_bad_patch_types_rejected(self):
        with self.assertRaises(ValueError):
            self.make_map([patch("same"), patch("same", x=1000)])
        with self.assertRaises(ValueError):
            self.make_map([{"patch_id": "x"}])

    def test_collection_is_detached(self):
        patches = [patch()]
        m = SurfaceMap(REV, 5000, 4000, patches)
        patches.clear()
        self.assertEqual(len(m.patches), 1)

    def test_schema_and_revision_validation(self):
        with self.assertRaises(ValueError):
            SurfaceMap(REV, 5000, 4000, (), schema="future")
        with self.assertRaises(ValueError):
            SurfaceMap("not-a-hash", 5000, 4000, ())

    def test_hostile_schema_string_is_rejected_before_equality(self):
        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("schema comparison ran before type admission")
            __hash__ = str.__hash__

        with self.assertRaisesRegex(ValueError, "unsupported surface schema"):
            SurfaceMap(REV, 5000, 4000, (), schema=HostileString(SCHEMA))


if __name__ == "__main__":
    unittest.main()
