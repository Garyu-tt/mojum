"""Hermetic adversarial tests for virtual, read-only CoM/CoP diagnostics."""
import copy
import json
import math
import unittest

from mojum.virtual_support import (
    INPUT_SCHEMA,
    MAX_CONTACTS,
    MAX_WIRE_I64,
    OUTPUT_SCHEMA,
    observe_virtual_support,
)


def sample():
    return {
        "schema": INPUT_SCHEMA,
        "tick": 7,
        "time_ns": 70_000_000,
        "backend": "fixture",
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "com_m": [0, 0, 0.9],
        "floor_contacts": [
            {"id": "left", "point_m": [-1, 0, 0], "vertical_force_n": 10},
            {"id": "right", "point_m": [1, 0, 0], "vertical_force_n": 30},
        ],
    }


class VirtualSupportTests(unittest.TestCase):
    def test_weighted_cop_and_engine_supplied_com(self):
        result = observe_virtual_support(sample())
        self.assertEqual(result["schema"], OUTPUT_SCHEMA)
        self.assertEqual(result["com_m"], [0.0, 0.0, 0.9])
        self.assertEqual(result["cop_xy_m"], [0.5, 0.0])
        self.assertEqual(result["total_vertical_force_n"], 40.0)
        self.assertEqual(result["contact_count"], 2)
        self.assertEqual(result["loaded_contact_count"], 2)
        self.assertEqual(len(result["input_sha256"]), 64)
        self.assertFalse(result["physical_contact"])
        self.assertFalse(result["physical_actuation"])
        json.dumps(result, allow_nan=False)

    def test_empty_and_unloaded_contacts_have_no_cop(self):
        p = sample()
        p["floor_contacts"] = []
        r = observe_virtual_support(p)
        self.assertIsNone(r["cop_xy_m"])
        self.assertEqual(r["total_vertical_force_n"], 0)
        p = sample()
        for contact in p["floor_contacts"]:
            contact["vertical_force_n"] = 0
        r = observe_virtual_support(p)
        self.assertIsNone(r["cop_xy_m"])
        self.assertEqual(r["loaded_contact_count"], 0)

    def test_contact_order_does_not_change_output_or_digest(self):
        p = sample()
        r1 = observe_virtual_support(p)
        p["floor_contacts"].reverse()
        r2 = observe_virtual_support(p)
        self.assertEqual(r1, r2)

    def test_detached_and_checksum_changes_on_measurement(self):
        p = sample()
        old = observe_virtual_support(p)
        p["com_m"][0] = 100
        p["floor_contacts"][0]["point_m"][0] = -2
        new = observe_virtual_support(p)
        self.assertEqual(old["com_m"], [0.0, 0.0, 0.9])
        self.assertNotEqual(old["input_sha256"], new["input_sha256"])

    def test_rejects_hardware_claims_and_unknown_schema(self):
        for field, value in (
            ("synthetic", False),
            ("physical_contact", True),
            ("physical_actuation", True),
            ("schema", "mojum.virtual-physics-support-input/2"),
            ("backend", "real_robot"),
            ("backend", 1),
        ):
            with self.subTest(field=field, value=value):
                p = sample()
                p[field] = value
                with self.assertRaises(ValueError):
                    observe_virtual_support(p)

    def test_rejects_clock_spoofing_and_overflow(self):
        for field in ("tick", "time_ns"):
            for value in (-1, True, 0.1, "7", MAX_WIRE_I64 + 1):
                with self.subTest(field=field, value=value):
                    p = sample()
                    p[field] = value
                    with self.assertRaises(ValueError):
                        observe_virtual_support(p)
            p = sample()
            p[field] = MAX_WIRE_I64
            self.assertEqual(observe_virtual_support(p)[field], MAX_WIRE_I64)

    def test_rejects_nonfinite_boolean_and_huge_numbers(self):
        for value in (True, False, math.nan, math.inf, -math.inf, 10**1000, 10001):
            for field, path in (
                ("com", ("com_m", 0)),
                ("point", ("floor_contacts", 0, "point_m", 1)),
                ("force", ("floor_contacts", 0, "vertical_force_n")),
            ):
                with self.subTest(field=field, value=str(value)[:10]):
                    p = sample()
                    if field == "com":
                        p["com_m"][0] = value
                    elif field == "point":
                        p["floor_contacts"][0]["point_m"][1] = value
                    else:
                        p["floor_contacts"][0]["vertical_force_n"] = value
                    with self.assertRaises(ValueError):
                        observe_virtual_support(p)

    def test_rejects_negative_force_duplicate_ids_and_unexpected_keys(self):
        bad = sample()
        bad["floor_contacts"][0]["vertical_force_n"] = -0.1
        with self.assertRaises(ValueError):
            observe_virtual_support(bad)
        bad = sample()
        bad["floor_contacts"][1]["id"] = "left"
        with self.assertRaises(ValueError):
            observe_virtual_support(bad)
        bad = sample()
        bad["floor_contacts"][0]["id"] = "../floor"
        with self.assertRaises(ValueError):
            observe_virtual_support(bad)
        bad = sample()
        bad["unsafe"] = 1
        with self.assertRaises(ValueError):
            observe_virtual_support(bad)
        bad = sample()
        bad["floor_contacts"][0]["unexpected"] = 1
        with self.assertRaises(ValueError):
            observe_virtual_support(bad)

    def test_rejects_malformed_vectors_and_contact_collections(self):
        for value in (None, {}, (0, 0, 0), [0, 0], [0, 0, 0, 0]):
            p = sample()
            p["com_m"] = value
            with self.subTest(value=str(value)):
                with self.assertRaises(ValueError):
                    observe_virtual_support(p)
        for value in (None, {}, [None], [3], [sample()["floor_contacts"][0]] * (MAX_CONTACTS + 1)):
            p = sample()
            p["floor_contacts"] = value
            with self.subTest(collection=str(type(value))):
                with self.assertRaises(ValueError):
                    observe_virtual_support(p)

    def test_order_boundary_and_valid_max_contacts(self):
        p = sample()
        p["floor_contacts"] = [
            {"id": "p%03d" % i, "point_m": [i, -i, 0], "vertical_force_n": 1}
            for i in range(MAX_CONTACTS)
        ]
        r = observe_virtual_support(p)
        self.assertEqual(r["contact_count"], MAX_CONTACTS)
        self.assertEqual(r["cop_xy_m"], [63.5, -63.5])
        p["floor_contacts"].append(copy.deepcopy(p["floor_contacts"][0]))
        with self.assertRaises(ValueError):
            observe_virtual_support(p)

    def test_malformed_envelope(self):
        for bad in (None, [], "{}", {"schema": INPUT_SCHEMA}):
            with self.assertRaises(ValueError):
                observe_virtual_support(bad)


if __name__ == "__main__":
    unittest.main()
