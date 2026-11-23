import unittest
from mojum.lerobot_trajectory import (
    CONSENT, MAX_FRAMES, MAX_JOINTS, POSITION_UNIT, PROVENANCE, SCHEMA,
    TrajectoryError, validate_trajectory,
)


def sample():
    return {
        "schema": SCHEMA, "provenance": PROVENANCE, "consent": CONSENT,
        "position_unit": POSITION_UNIT, "joint_names": ["shoulder", "elbow"],
        "frames": [
            {"timestamp_ns": 10, "positions_rad": [0, 0.25]},
            {"timestamp_ns": 20, "positions_rad": [0.5, -0.25]},
        ],
    }


class TrajectoryAdmissionTests(unittest.TestCase):
    def test_valid_result_and_summary(self):
        result = validate_trajectory(sample())
        self.assertEqual((result.frame_count, result.joint_count, result.duration_ns), (2, 2, 10))
        self.assertEqual(result.joint_names, ("shoulder", "elbow"))
        self.assertEqual(result.positions_rad[1], (0.5, -0.25))
        self.assertEqual(len(result.sha256), 64)

    def test_digest_is_deterministic_and_normalizes_negative_zero(self):
        positive, negative = sample(), sample()
        negative["frames"][0]["positions_rad"][0] = -0.0
        self.assertEqual(validate_trajectory(positive).sha256,
                         validate_trajectory(negative).sha256)

    def test_result_is_detached_and_immutable(self):
        payload = sample()
        result = validate_trajectory(payload)
        payload["joint_names"][0] = "changed"
        payload["frames"][0]["positions_rad"][0] = 99
        payload["frames"].clear()
        self.assertEqual(result.joint_names[0], "shoulder")
        self.assertEqual(result.positions_rad[0][0], 0.0)
        with self.assertRaises((AttributeError, TypeError)):
            result.frame_count = 5
        with self.assertRaises(TypeError):
            result.positions_rad[0][0] = 5

    def test_exact_root_keys_required(self):
        for mutate in (
            lambda p: p.update(extra=True),
            lambda p: p.pop("consent"),
        ):
            payload = sample()
            mutate(payload)
            with self.assertRaises(TrajectoryError):
                validate_trajectory(payload)

    def test_rejects_oversized_root_and_frame_mappings(self):
        payload = sample()
        payload.update({f"unexpected_{i}": i for i in range(25_000)})
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

        payload = sample()
        payload["frames"][0].update({f"unexpected_{i}": i for i in range(25_000)})
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_rejects_non_native_root_and_frame_keys_before_comparison(self):
        class HostileKey:
            def __init__(self, target):
                self.target = target
            def __hash__(self):
                return hash(self.target)
            def __eq__(self, other):
                raise AssertionError("hostile key equality must not run")

        payload = sample()
        payload.pop("schema")
        payload[HostileKey("schema")] = SCHEMA
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

        payload = sample()
        frame = payload["frames"][0]
        frame.pop("timestamp_ns")
        frame[HostileKey("timestamp_ns")] = 10
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_provenance_and_consent_required(self):
        for field, value in (("provenance", "PRIVATE"), ("consent", "implicit")):
            payload = sample()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(TrajectoryError):
                validate_trajectory(payload)

    def test_subclassed_containers_rejected(self):
        class CustomDict(dict):
            pass
        class CustomList(list):
            pass
        with self.assertRaises(TrajectoryError):
            validate_trajectory(CustomDict(sample()))
        payload = sample()
        payload["joint_names"] = CustomList(payload["joint_names"])
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_duplicate_or_bad_joint_names_rejected(self):
        for names in (["same", "same"], ["ok", " bad"], ["", "x"]):
            payload = sample()
            payload["joint_names"] = names
            with self.subTest(names=names), self.assertRaises(TrajectoryError):
                validate_trajectory(payload)

    def test_bool_float_and_out_of_range_timestamps_rejected(self):
        for value in (True, -1, 1 << 63, 1.5):
            payload = sample()
            payload["frames"][0]["timestamp_ns"] = value
            with self.subTest(value=value), self.assertRaises(TrajectoryError):
                validate_trajectory(payload)

    def test_non_monotonic_timestamps_rejected(self):
        payload = sample()
        payload["frames"][1]["timestamp_ns"] = 10
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_position_dimension_must_match_joint_names(self):
        payload = sample()
        payload["frames"][1]["positions_rad"] = [1]
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_nonfinite_bool_and_extreme_positions_rejected(self):
        for value in (True, float("nan"), float("inf"), -float("inf"), 1e30):
            payload = sample()
            payload["frames"][0]["positions_rad"][0] = value
            with self.subTest(value=value), self.assertRaises(TrajectoryError):
                validate_trajectory(payload)

    def test_schema_and_units_are_versioned(self):
        for field, value in (("schema", "v2"), ("position_unit", "degrees")):
            payload = sample()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(TrajectoryError):
                validate_trajectory(payload)

    def test_frame_and_joint_budgets(self):
        payload = sample()
        payload["frames"] = [
            {"timestamp_ns": i, "positions_rad": [0, 0]}
            for i in range(MAX_FRAMES + 1)
        ]
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)
        payload = sample()
        payload["joint_names"] = [f"j{i}" for i in range(MAX_JOINTS + 1)]
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_joint_order_is_semantically_significant(self):
        original = sample()
        reordered = sample()
        reordered["joint_names"] = ["elbow", "shoulder"]
        for frame in reordered["frames"]:
            frame["positions_rad"].reverse()
        self.assertNotEqual(
            validate_trajectory(original).sha256,
            validate_trajectory(reordered).sha256,
        )

    def test_scalar_sample_budget_is_enforced(self):
        payload = {
            "schema": SCHEMA, "provenance": PROVENANCE, "consent": CONSENT,
            "position_unit": POSITION_UNIT,
            "joint_names": [f"j{i}" for i in range(MAX_JOINTS)],
            "frames": [
                {"timestamp_ns": i, "positions_rad": [0] * MAX_JOINTS}
                for i in range(1001)
            ],
        }
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_mapping_keys_are_bounded_before_set_comparison(self):
        payload = sample()
        payload.update({f"extra-{index}": index for index in range(10_000)})
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_hostile_string_subclass_keys_fail_closed(self):
        class HostileString(str):
            block_hash = False

            def __hash__(self):
                if type(self).block_hash:
                    raise RuntimeError("hostile hash must not execute")
                return str.__hash__(self)

        payload = sample()
        payload["hostile-temp"] = 1
        payload.pop("hostile-temp")
        payload.pop("schema")
        payload[HostileString("schema")] = SCHEMA
        HostileString.block_hash = True
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

        HostileString.block_hash = False
        payload.pop(HostileString("schema"))
        payload["schema"] = SCHEMA
        frame = payload["frames"][0]
        frame.pop("timestamp_ns")
        frame[HostileString("timestamp_ns")] = 10
        HostileString.block_hash = True
        with self.assertRaises(TrajectoryError):
            validate_trajectory(payload)

    def test_bad_root_values_rejected(self):
        for payload in (None, [], "trajectory", 42):
            with self.subTest(payload=payload), self.assertRaises(TrajectoryError):
                validate_trajectory(payload)


if __name__ == "__main__":
    unittest.main()
