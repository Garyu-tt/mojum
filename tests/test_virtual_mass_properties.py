import math
import unittest

from mojum.virtual_mass_properties import (
    MassPropertiesError,
    build_mass_properties,
)


IDENTITY = [
    [1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [0.0, 0.0, 1.0],
]


def segment(identifier, mass=1.0, center=(0.0, 0.0, 0.0), inertia=None,
            rotation=None, translation=(0.0, 0.0, 0.0)):
    if inertia is None:
        inertia = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return {
        "id": identifier,
        "mass_kg": mass,
        "center_m": list(center),
        "inertia_kg_m2": [list(row) for row in inertia],
        "rotation": [list(row) for row in (rotation or IDENTITY)],
        "translation_m": list(translation),
    }


def fixture(*segments):
    return {"schema": "mojum.virtual-mass-properties/1",
            "visibility": "PUBLIC_SYNTHETIC",
            "segments": list(segments)}


class VirtualMassPropertiesTests(unittest.TestCase):
    def test_single_segment_identity_transform(self):
        source = fixture(segment("torso", mass=4.0, center=(0.25, 0.0, 0.0),
                                 inertia=[[2, 0, 0], [0, 3, 0], [0, 0, 4]],
                                 translation=(1.0, 2.0, 3.0)))
        result = build_mass_properties(source)
        self.assertEqual(result.segment_count, 1)
        self.assertEqual(result.total_mass_kg, 4.0)
        self.assertEqual(result.center_of_mass_m, (1.25, 2.0, 3.0))
        self.assertEqual(result.inertia_about_com_kg_m2,
                         ((2.0, 0.0, 0.0), (0.0, 3.0, 0.0), (0.0, 0.0, 4.0)))

    def test_parallel_axis_theorem_two_equal_masses(self):
        source = fixture(
            segment("left", mass=2.0, translation=(-1.0, 0.0, 0.0),
                    inertia=[[0, 0, 0], [0, 0, 0], [0, 0, 0]]),
            segment("right", mass=2.0, translation=(1.0, 0.0, 0.0),
                    inertia=[[0, 0, 0], [0, 0, 0], [0, 0, 0]]),
        )
        result = build_mass_properties(source)
        self.assertEqual(result.center_of_mass_m, (0.0, 0.0, 0.0))
        self.assertEqual(result.inertia_about_com_kg_m2,
                         ((0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0)))

    def test_tiny_negative_inertia_is_rejected(self):
        # Relative scaling matters: this invalid principal moment is tiny in SI
        # units, but is 100% negative relative to the tensor's own magnitude.
        tensor = [[-1e-11, 0, 0], [0, 0, 0], [0, 0, 0]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", inertia=tensor)))

    def test_tiny_asymmetry_is_rejected_relative_to_tensor_scale(self):
        tensor = [[1e-12, 2e-11, 0], [0, 1e-12, 0], [0, 0, 1e-12]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", inertia=tensor)))

    def test_mass_weighted_center_of_mass(self):
        source = fixture(
            segment("light", mass=1.0, translation=(0.0, 0.0, 0.0)),
            segment("heavy", mass=3.0, translation=(4.0, 0.0, 0.0)),
        )
        result = build_mass_properties(source)
        self.assertEqual(result.total_mass_kg, 4.0)
        self.assertEqual(result.center_of_mass_m, (3.0, 0.0, 0.0))

    def test_rotation_changes_world_inertia(self):
        rotation_z_90 = [
            [0.0, -1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
        source = fixture(segment(
            "link", inertia=[[1, 0, 0], [0, 2, 0], [0, 0, 3]],
            rotation=rotation_z_90,
        ))
        result = build_mass_properties(source)
        self.assertAlmostEqual(result.inertia_about_com_kg_m2[0][0], 2.0)
        self.assertAlmostEqual(result.inertia_about_com_kg_m2[1][1], 1.0)
        self.assertAlmostEqual(result.inertia_about_com_kg_m2[2][2], 3.0)

    def test_input_order_does_not_change_digest_or_result(self):
        a = segment("a", mass=2.0, translation=(1.0, 0.0, 0.0))
        b = segment("b", mass=3.0, translation=(-2.0, 0.0, 0.0))
        first = build_mass_properties(fixture(a, b))
        second = build_mass_properties(fixture(b, a))
        self.assertEqual(first, second)

    def test_result_is_detached(self):
        source = fixture(segment("link"))
        result = build_mass_properties(source)
        projection = result.to_dict()
        projection["center_of_mass_m"][0] = 999.0
        projection["inertia_about_com_kg_m2"][0][0] = 999.0
        self.assertEqual(result.center_of_mass_m, (0.0, 0.0, 0.0))
        self.assertEqual(result.inertia_about_com_kg_m2[0][0], 1.0)

    def test_zero_mass_is_rejected(self):
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", mass=0.0)))

    def test_bool_is_not_a_number(self):
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", mass=True)))

    def test_nonfinite_position_is_rejected(self):
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", translation=(math.inf, 0, 0))))

    def test_non_native_container_is_rejected(self):
        class DictSubclass(dict):
            pass
        class ListSubclass(list):
            pass
        raw = fixture(segment("ok"))
        raw["segments"] = ListSubclass(raw["segments"])
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)
        hostile = DictSubclass(fixture(segment("ok")))
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(hostile)

    def test_duplicate_segment_id_is_rejected(self):
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("same"), segment("same")))

    def test_unknown_and_missing_fields_are_rejected(self):
        raw = fixture(segment("link"))
        raw["surprise"] = 1
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)
        raw = fixture(segment("link"))
        del raw["segments"][0]["translation_m"]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)

    def test_oversized_fixed_schema_mappings_rejected_before_key_snapshot(self):
        raw = fixture(segment("ok"))
        raw.update({f"extra-{index}": index for index in range(50_000)})
        with self.assertRaisesRegex(MassPropertiesError, "missing or unknown fields"):
            build_mass_properties(raw)

        raw = fixture(segment("ok"))
        raw["segments"][0].update({
            f"extra-{index}": index for index in range(50_000)
        })
        with self.assertRaisesRegex(MassPropertiesError, "missing or unknown fields"):
            build_mass_properties(raw)

    def test_oversized_fixed_schema_key_rejected_before_set_hashing(self):
        from unittest.mock import patch

        raw = fixture(segment("ok"))
        raw.pop("schema")
        raw["s" * 100_000] = "mojum.virtual-mass-properties/1"

        def guarded_set(values=()):
            values = tuple(values)
            if any(type(key) is str and len(key) > 32 for key in values):
                raise AssertionError("oversized schema key reached set hashing")
            return set(values)

        with patch("mojum.virtual_mass_properties.set", side_effect=guarded_set, create=True):
            with self.assertRaisesRegex(MassPropertiesError, "key length"):
                build_mass_properties(raw)

    def test_non_symmetric_inertia_is_rejected(self):
        tensor = [[1, 0.1, 0], [0, 1, 0], [0, 0, 1]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", inertia=tensor)))

    def test_indefinite_inertia_is_rejected(self):
        tensor = [[1, 2, 0], [2, 1, 0], [0, 0, 1]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", inertia=tensor)))

    def test_inertia_triangle_inequality_is_enforced(self):
        # PSD alone is insufficient: principal moments must be physically realizable.
        tensor = [[1, 0, 0], [0, 1, 0], [0, 0, 5]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", inertia=tensor)))

    def test_hostile_non_string_mapping_key_is_rejected_before_comparison(self):
        raw = fixture(segment("ok"))
        # The hostile key must not collide with an existing str key while the
        # fixture is constructed; otherwise dict insertion invokes __eq__
        # before build_mass_properties() gets a chance to reject the key.
        occupied_hashes = {hash(key) for key in dict.keys(raw)}
        hostile_hash = 0
        while hostile_hash in occupied_hashes:
            hostile_hash += 1

        class HostileKey:
            def __hash__(self):
                return hostile_hash
            def __eq__(self, other):
                raise AssertionError("hostile equality must not run")

        raw[HostileKey()] = "unexpected"
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)

    def test_reflection_is_not_a_rotation(self):
        reflection = [[-1, 0, 0], [0, 1, 0], [0, 0, 1]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", rotation=reflection)))

    def test_non_orthonormal_transform_is_rejected(self):
        invalid = [[1, 0, 0], [0, 2, 0], [0, 0, 1]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(segment("bad", rotation=invalid)))

    def test_private_visibility_is_rejected(self):
        raw = fixture(segment("link"))
        raw["visibility"] = "PRIVATE"
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)

    def test_segment_and_total_budgets_are_enforced(self):
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(*(segment(f"s{i}", mass=10_000.0) for i in range(11))))
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(fixture(*(segment(f"s{i}") for i in range(65))))

    def test_tensor_must_have_three_rows_and_three_columns(self):
        raw = fixture(segment("link"))
        raw["segments"][0]["inertia_kg_m2"] = [[1, 0], [0, 1]]
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)

    def test_schema_and_visibility_are_exact_native_strings(self):
        raw = fixture(segment("link"))
        class HostileString(str):
            def __eq__(self, other):
                raise AssertionError("equality must not be invoked")
        raw["schema"] = HostileString("mojum.virtual-mass-properties/1")
        with self.assertRaises(MassPropertiesError):
            build_mass_properties(raw)

    def test_digest_is_sha256_shaped(self):
        result = build_mass_properties(fixture(segment("link")))
        self.assertEqual(len(result.digest), 64)
        self.assertEqual(len(result.input_digest), 64)
        int(result.digest, 16)
        int(result.input_digest, 16)


if __name__ == "__main__":
    unittest.main()
