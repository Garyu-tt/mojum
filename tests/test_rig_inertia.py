"""Adversarial, offline tests for the synthetic rig inertial validator."""
from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError

from mojum.rig_inertia import (
    RigInertiaError,
    validate_rig_inertia,
)


def valid_link(link_id="pelvis"):
    return {
        "id": link_id,
        "mass_kg": 4.0,
        "com_m": [0.0, 0.01, -0.02],
        "inertia_kg_m2": [
            [1.0, 0.0, 0.0],
            [0.0, 1.2, 0.0],
            [0.0, 0.0, 1.4],
        ],
    }


def packet(links=None):
    return {
        "schema": "mojum.rig-inertia/1",
        "scope": "PUBLIC_SYNTHETIC",
        "unit_system": "SI",
        "links": [valid_link()] if links is None else links,
    }


class DictSubclass(dict):
    pass


class ListSubclass(list):
    pass


class RigInertiaTests(unittest.TestCase):
    def test_valid_record_and_digest_shape(self):
        result = validate_rig_inertia(packet())
        self.assertEqual(result.schema, "mojum.rig-inertia/1")
        self.assertEqual(len(result.links), 1)
        self.assertEqual(result.links[0].mass_kg, 4.0)
        self.assertRegex(result.sha256, r"^[0-9a-f]{64}$")

    def test_order_is_canonical(self):
        a = validate_rig_inertia(packet([valid_link("zeta"), valid_link("alpha")]))
        b = validate_rig_inertia(packet([valid_link("alpha"), valid_link("zeta")]))
        self.assertEqual(a.sha256, b.sha256)
        self.assertEqual([x.link_id for x in a.links], ["alpha", "zeta"])

    def test_mapping_export_is_detached(self):
        result = validate_rig_inertia(packet())
        exported = result.as_dict()
        exported["links"][0]["com_m"][0] = 999
        self.assertEqual(result.links[0].center_of_mass_m[0], 0.0)

    def test_result_is_frozen(self):
        result = validate_rig_inertia(packet())
        with self.assertRaises(FrozenInstanceError):
            result.sha256 = "0" * 64

    def test_wrong_schema_rejected(self):
        value = packet()
        value["schema"] = "mojum.rig-inertia/2"
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)

    def test_non_public_scope_rejected(self):
        value = packet()
        value["scope"] = "PRIVATE"
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)

    def test_unknown_top_level_key_rejected(self):
        value = packet()
        value["notes"] = "must not enter digest"
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)

    def test_unknown_link_key_rejected(self):
        link = valid_link()
        link["name"] = "extra"
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(packet([link]))

    def test_empty_and_oversized_inventory_rejected(self):
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(packet([]))
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(packet([valid_link(str(i)) for i in range(257)]))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(packet([valid_link("same"), valid_link("same")]))

    def test_invalid_id_rejected(self):
        for link_id in ("", "../pelvis", " leading", "a/b", "a\nb", "x" * 65):
            with self.subTest(link_id=link_id):
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(packet([valid_link(link_id)]))

    def test_mass_must_be_positive(self):
        for mass in (0, -1, True):
            with self.subTest(mass=mass):
                value = packet()
                value["links"][0]["mass_kg"] = mass
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(value)

    def test_mass_rejects_nonfinite_and_unbounded(self):
        for mass in (float("nan"), float("inf"), 1_000_001, 10**1000):
            with self.subTest(mass=repr(mass)[:30]):
                value = packet()
                value["links"][0]["mass_kg"] = mass
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(value)

    def test_com_requires_exact_three_finite_values(self):
        for center in ([0, 0], [0, 0, 0, 0], (0, 0, 0),
                       [0, float("nan"), 0], [0, True, 0],
                       [0, 0, 1001]):
            with self.subTest(center=center):
                value = packet()
                value["links"][0]["com_m"] = center
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(value)

    def test_inertia_requires_three_by_three_list(self):
        for matrix in ([1, 2, 3], [[1, 0], [0, 1]], ((1, 0, 0),) * 3):
            with self.subTest(matrix=matrix):
                value = packet()
                value["links"][0]["inertia_kg_m2"] = matrix
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(value)

    def test_inertia_rejects_asymmetry(self):
        value = packet()
        value["links"][0]["inertia_kg_m2"][0][1] = 0.1
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)

    def test_inertia_must_be_positive_definite(self):
        matrices = (
            [[1, 0, 0], [0, -1, 0], [0, 0, 1]],
            [[1, 0, 0], [0, 0, 0], [0, 0, 1]],
        )
        for matrix in matrices:
            with self.subTest(matrix=matrix):
                value = packet()
                value["links"][0]["inertia_kg_m2"] = matrix
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(value)

    def test_principal_moments_must_obey_triangle_inequality(self):
        value = packet()
        value["links"][0]["inertia_kg_m2"] = [
            [1, 0, 0], [0, 1, 0], [0, 0, 3],
        ]
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)

    def test_valid_coupled_symmetric_tensor(self):
        value = packet()
        value["links"][0]["inertia_kg_m2"] = [
            [2.0, 0.1, 0.0],
            [0.1, 2.2, 0.05],
            [0.0, 0.05, 2.4],
        ]
        self.assertEqual(len(validate_rig_inertia(value).sha256), 64)

    def test_tensor_rejects_boolean_and_nonfinite_values(self):
        for bad in (True, float("nan"), float("inf"), 10**1000):
            with self.subTest(bad=repr(bad)[:30]):
                value = packet()
                value["links"][0]["inertia_kg_m2"][0][0] = bad
                with self.assertRaises(RigInertiaError):
                    validate_rig_inertia(value)

    def test_hostile_container_subclasses_rejected_before_iteration(self):
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(DictSubclass(packet()))
        value = packet()
        value["links"] = ListSubclass(value["links"])
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)

    def test_link_container_subclass_rejected(self):
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(packet([DictSubclass(valid_link())]))

    def test_bad_unit_system_rejected(self):
        value = packet()
        value["unit_system"] = "imperial"
        with self.assertRaises(RigInertiaError):
            validate_rig_inertia(value)


if __name__ == "__main__":
    unittest.main()
