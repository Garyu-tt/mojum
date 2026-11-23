"""Adversarial offline tests for the synthetic DOF-limit metadata contract."""
import copy
import unittest
from dataclasses import FrozenInstanceError

from mojum.rig_dof_limits import (
    DOFLimitsError, MAX_DOFS, SCHEMA, validate_dof_limits,
)


def valid_payload():
    return {
        "schema": SCHEMA,
        "rig_id": "dante-synthetic",
        "rig_revision": 3,
        "dofs": [
            {
                "id": "left_elbow_flex",
                "kind": "revolute",
                "unit": "rad",
                "axis": [0.0, 0.0, 1.0],
                "lower": -2.4,
                "upper": 0.1,
            },
            {
                "id": "root_slide",
                "kind": "prismatic",
                "unit": "m",
                "axis": [1.0, 0.0, 0.0],
                "lower": -0.25,
                "upper": 0.25,
            },
        ],
    }


class RigDOFLimitsTests(unittest.TestCase):
    def test_valid_mixed_dofs_roundtrip(self):
        result = validate_dof_limits(valid_payload())
        self.assertEqual(result.rig_id, "dante-synthetic")
        self.assertEqual(len(result.dofs), 2)
        restored = validate_dof_limits({
            k: v for k, v in result.to_dict().items() if k != "digest"
        })
        self.assertEqual(restored.digest, result.digest)

    def test_digest_is_deterministic(self):
        self.assertEqual(
            validate_dof_limits(valid_payload()).digest,
            validate_dof_limits(valid_payload()).digest,
        )

    def test_digest_binds_revision_and_order(self):
        original = validate_dof_limits(valid_payload()).digest
        changed = valid_payload()
        changed["rig_revision"] += 1
        self.assertNotEqual(original, validate_dof_limits(changed).digest)
        changed = valid_payload()
        changed["dofs"].reverse()
        self.assertNotEqual(original, validate_dof_limits(changed).digest)

    def test_result_is_immutable_and_export_is_detached(self):
        result = validate_dof_limits(valid_payload())
        with self.assertRaises(FrozenInstanceError):
            result.rig_id = "changed"
        exported = result.to_dict()
        exported["dofs"][0]["axis"][0] = 55
        self.assertEqual(result.dofs[0].axis, (0.0, 0.0, 1.0))

    def test_exact_schema_required(self):
        for schema in ("mojum.rig-dof-limits/2", None, 1, True):
            data = valid_payload()
            data["schema"] = schema
            with self.subTest(schema=schema), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_outer_unknown_and_missing_keys_rejected(self):
        data = valid_payload()
        data["surprise"] = 1
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)
        data = valid_payload()
        del data["rig_revision"]
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)

    def test_wrong_container_types_rejected(self):
        for value in (None, [], "payload", type("DictSubclass", (dict,), {})()):
            with self.subTest(value=type(value).__name__), self.assertRaises(DOFLimitsError):
                validate_dof_limits(value)

    def test_revision_rejects_bool_negative_and_overflow(self):
        for revision in (True, -1, 1 << 63, 3.0):
            data = valid_payload()
            data["rig_revision"] = revision
            with self.subTest(revision=revision), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_dof_count_is_bounded(self):
        data = valid_payload()
        data["dofs"] = data["dofs"] * (MAX_DOFS // 2 + 1)
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)
        data["dofs"] = []
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)

    def test_duplicate_and_invalid_ids_rejected(self):
        data = valid_payload()
        data["dofs"][1]["id"] = data["dofs"][0]["id"]
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)
        for invalid in ("", "../root", "bad id", "é", ".hidden", "x" * 65):
            data = valid_payload()
            data["dofs"][0]["id"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_joint_kind_unit_pair_is_exact(self):
        for kind, unit in (("revolute", "m"), ("prismatic", "rad"),
                           ("free", "m"), ("ball", "rad")):
            data = valid_payload()
            data["dofs"][0]["kind"] = kind
            data["dofs"][0]["unit"] = unit
            with self.subTest(kind=kind, unit=unit), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_axis_requires_three_finite_numeric_values(self):
        for axis in ([1.0, 0.0], [0.0, 0.0, 0.0],
                     [float("nan"), 0.0, 1.0], [float("inf"), 0.0, 0.0],
                     [True, 0.0, 0.0], ["1", 0.0, 0.0]):
            data = valid_payload()
            data["dofs"][0]["axis"] = axis
            with self.subTest(axis=axis), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_axis_must_be_normalized_not_silently_repaired(self):
        data = valid_payload()
        data["dofs"][0]["axis"] = [0.0, 0.0, 2.0]
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)

    def test_limits_reject_equal_inverted_and_non_finite(self):
        for lower, upper in ((0.0, 0.0), (1.0, 0.0),
                             (float("nan"), 1.0), (0.0, float("inf"))):
            data = valid_payload()
            data["dofs"][0]["lower"] = lower
            data["dofs"][0]["upper"] = upper
            with self.subTest(lower=lower, upper=upper), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_numeric_bounds_reject_bool_and_numeric_strings(self):
        for lower in (True, "0", None):
            data = valid_payload()
            data["dofs"][0]["lower"] = lower
            with self.subTest(lower=lower), self.assertRaises(DOFLimitsError):
                validate_dof_limits(data)

    def test_strict_nested_keys(self):
        data = valid_payload()
        data["dofs"][0]["extra"] = 1
        with self.assertRaises(DOFLimitsError):
            validate_dof_limits(data)

    def test_caller_mutation_does_not_change_result(self):
        data = valid_payload()
        result = validate_dof_limits(data)
        data["dofs"][0]["axis"][2] = 0.0
        data["dofs"].clear()
        self.assertEqual(len(result.dofs), 2)


if __name__ == "__main__":
    unittest.main()
