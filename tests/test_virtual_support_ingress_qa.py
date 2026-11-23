"""QA for hostile dictionary keys and bounded contact IDs in virtual support.

Synthetic-only: no engine, physical actuation, network or private data.
"""
import unittest
from unittest.mock import patch

from mojum.virtual_support import INPUT_SCHEMA, observe_virtual_support


class HostileString(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise AssertionError("hostile key equality was invoked")


def packet():
    return {
        "schema": INPUT_SCHEMA,
        "tick": 1,
        "time_ns": 20_000_000,
        "backend": "fixture",
        "synthetic": True,
        "physical_contact": False,
        "physical_actuation": False,
        "com_m": [0, 0, 1],
        "floor_contacts": [
            {"id": "left", "point_m": [-1, 0, 0], "vertical_force_n": 10},
            {"id": "right", "point_m": [1, 0, 0], "vertical_force_n": 30},
        ],
    }


class VirtualSupportIngressQATests(unittest.TestCase):
    def test_hostile_top_level_mapping_key_never_runs_equality(self):
        sample = packet()
        value = sample.pop("schema")
        sample[HostileString("schema")] = value
        with self.assertRaisesRegex(ValueError, "invalid support envelope"):
            observe_virtual_support(sample)

    def test_hostile_contact_mapping_key_never_runs_equality(self):
        sample = packet()
        contact = sample["floor_contacts"][0]
        value = contact.pop("id")
        contact[HostileString("id")] = value
        with self.assertRaisesRegex(ValueError, "invalid virtual floor contact"):
            observe_virtual_support(sample)

    def test_nonstring_mapping_keys_are_rejected(self):
        sample = packet()
        sample[1] = "unexpected"
        with self.assertRaises(ValueError):
            observe_virtual_support(sample)
        sample = packet()
        sample["floor_contacts"][0][1] = "unexpected"
        with self.assertRaises(ValueError):
            observe_virtual_support(sample)

    def test_oversized_exact_contact_id_fails_before_regex_scan(self):
        sample = packet()
        sample["floor_contacts"][0]["id"] = "x" * 100_000
        with patch("mojum.virtual_support._ID") as matcher:
            matcher.fullmatch.side_effect = AssertionError("giant ID reached regex")
            with self.assertRaisesRegex(ValueError, "invalid or duplicate contact id"):
                observe_virtual_support(sample)
            matcher.fullmatch.assert_not_called()

    def test_normal_weighted_cop_is_unchanged(self):
        result = observe_virtual_support(packet())
        self.assertEqual(result["cop_xy_m"], [0.5, 0.0])
        self.assertEqual(result["total_vertical_force_n"], 40.0)
        self.assertEqual(result["contact_count"], 2)
        self.assertEqual(len(result["input_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
