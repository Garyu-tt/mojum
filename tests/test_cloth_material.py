"""Adversarial tests for the solver-neutral cloth material profile."""
import unittest

from mojum.cloth_material import (
    ClothMaterialError,
    ClothMaterialProfile,
    validate_material_profile,
)


def valid_profile():
    return {
        "schema": "mojum.cloth.material-profile/1",
        "classification": "PUBLIC_SYNTHETIC",
        "areal_density_ug_m2": 180_000_000,
        "thickness_nm": 250_000,
        "stretch_stiffness_uN_m": 12_000_000,
        "bend_stiffness_uNm": 75,
        "damping_ratio_ppm": 20_000,
        "static_friction_ppm": 450_000,
        "dynamic_friction_ppm": 300_000,
    }


class IntSubclass(int):
    pass


class HostileMapping(dict):
    def __iter__(self):
        raise AssertionError("mapping iteration must not be invoked")


class HostileKey:
    def __hash__(self):
        return 7

    def __eq__(self, other):
        raise AssertionError("hostile key comparison must not be invoked")


class HostileString(str):
    def __eq__(self, other):
        raise AssertionError("hostile string equality must not be invoked")


class ClothMaterialProfileTests(unittest.TestCase):
    def test_valid_profile_and_fixed_point_units(self):
        profile = validate_material_profile(valid_profile())
        self.assertEqual(profile.areal_density_ug_m2, 180_000_000)
        self.assertEqual(profile.thickness_nm, 250_000)
        self.assertEqual(profile.schema, ClothMaterialProfile.SCHEMA)
        self.assertEqual(len(profile.fingerprint), 64)

    def test_fingerprint_is_order_independent(self):
        first = valid_profile()
        second = dict(reversed(list(first.items())))
        self.assertEqual(
            validate_material_profile(first).fingerprint,
            validate_material_profile(second).fingerprint,
        )

    def test_fingerprint_changes_with_any_coefficient(self):
        first = validate_material_profile(valid_profile())
        changed = valid_profile()
        changed["bend_stiffness_uNm"] += 1
        second = validate_material_profile(changed)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_to_mapping_is_detached(self):
        profile = validate_material_profile(valid_profile())
        exported = profile.to_mapping()
        exported["areal_density_ug_m2"] = 1
        self.assertEqual(profile.areal_density_ug_m2, 180_000)

    def test_dataclass_is_immutable(self):
        profile = validate_material_profile(valid_profile())
        with self.assertRaises(AttributeError):
            profile.thickness_nm = 1

    def test_rejects_oversized_native_dict_before_key_snapshot(self):
        value = valid_profile()
        value.update({f"unexpected_{index}": index for index in range(20_000)})
        with self.assertRaisesRegex(
            ClothMaterialError, "fields do not match the versioned schema"
        ):
            validate_material_profile(value)

    def test_rejects_mapping_subclass_before_iteration(self):
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(HostileMapping(valid_profile()))

    def test_rejects_non_string_hostile_key_before_comparison(self):
        value = valid_profile()
        value[HostileKey()] = 1
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(value)

    def test_rejects_string_subclass_schema_before_equality(self):
        value = valid_profile()
        value["schema"] = HostileString("mojum.cloth.material-profile/1")
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(value)

    def test_rejects_missing_and_unknown_fields(self):
        value = valid_profile()
        del value["bend_stiffness_uNm"]
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(value)
        value = valid_profile()
        value["surprise"] = 1
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(value)

    def test_rejects_wrong_schema_and_classification(self):
        for field, bad in (
            ("schema", "mojum.cloth.material-profile/2"),
            ("classification", "PRIVATE"),
        ):
            value = valid_profile()
            value[field] = bad
            with self.subTest(field=field), self.assertRaises(ClothMaterialError):
                validate_material_profile(value)

    def test_rejects_non_native_numeric_values(self):
        for bad in (True, IntSubclass(1), 1.0, "1"):
            value = valid_profile()
            value["areal_density_ug_m2"] = bad
            with self.subTest(value=repr(bad)), self.assertRaises(ClothMaterialError):
                validate_material_profile(value)

    def test_rejects_each_out_of_range_parameter(self):
        cases = (
            ("areal_density_ug_m2", 0),
            ("areal_density_ug_m2", 1_000_000_000_001),
            ("thickness_nm", 0),
            ("thickness_nm", 1_000_000_001),
            ("stretch_stiffness_uN_m", 0),
            ("stretch_stiffness_uN_m", 1_000_000_000_000_001),
            ("bend_stiffness_uNm", -1),
            ("bend_stiffness_uNm", 1_000_000_000_001),
            ("damping_ratio_ppm", -1),
            ("damping_ratio_ppm", 1_000_001),
            ("static_friction_ppm", -1),
            ("static_friction_ppm", 10_000_001),
            ("dynamic_friction_ppm", -1),
            ("dynamic_friction_ppm", 10_000_001),
        )
        for field, bad in cases:
            value = valid_profile()
            value[field] = bad
            with self.subTest(field=field, value=bad), self.assertRaises(ClothMaterialError):
                validate_material_profile(value)

    def test_rejects_huge_integer_without_float_conversion(self):
        value = valid_profile()
        value["stretch_stiffness_uN_m"] = 10 ** 10_000
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(value)

    def test_rejects_dynamic_friction_above_static(self):
        value = valid_profile()
        value["dynamic_friction_ppm"] = value["static_friction_ppm"] + 1
        with self.assertRaises(ClothMaterialError):
            validate_material_profile(value)

    def test_rejects_non_dict_input(self):
        for value in (None, [], (), "profile", valid_profile().items()):
            with self.subTest(value=type(value).__name__), self.assertRaises(ClothMaterialError):
                validate_material_profile(value)

    def test_direct_constructor_enforces_contract(self):
        value = valid_profile()
        value["areal_density_ug_m2"] = True
        with self.assertRaises(ClothMaterialError):
            ClothMaterialProfile(**value)


if __name__ == "__main__":
    unittest.main()
