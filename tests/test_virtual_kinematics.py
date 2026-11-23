import copy
import math
import unittest

from mojum.virtual_kinematics import (
    evaluate_forward_kinematics,
    verify_kinematics_report,
)


def joint(identifier, parent, origin=(0, 0, 0), axis=(0, 0, 1),
          angle=0.0, minimum=-math.pi, maximum=math.pi):
    return {
        "id": identifier,
        "parent": parent,
        "origin_m": list(origin),
        "axis": list(axis),
        "angle_rad": angle,
        "min_rad": minimum,
        "max_rad": maximum,
    }


def sample():
    return {
        "schema": "mojum.virtual-rig/1",
        "visibility": "PUBLIC_SYNTHETIC",
        "root": "pelvis",
        "joints": [
            joint("pelvis", None),
            joint("spine", "pelvis", origin=(0, 0, 1), axis=(0, 0, 1),
                  angle=math.pi / 2),
            joint("hand", "spine", origin=(1, 0, 0), axis=(0, 1, 0)),
        ],
    }


class VirtualKinematicsTests(unittest.TestCase):
    def test_hierarchical_translation_uses_parent_rotation(self):
        report = evaluate_forward_kinematics(sample())
        hand = next(item for item in report["joints"] if item["id"] == "hand")
        self.assertEqual(hand["position_m"], [0.0, 1.0, 1.0])

    def test_root_has_identity_transform(self):
        report = evaluate_forward_kinematics(sample())
        root = report["joints"][0]
        self.assertEqual(root["position_m"], [0.0, 0.0, 0.0])
        self.assertEqual(root["rotation_matrix"], [
            [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
        ])

    def test_rotation_matrix_is_orthonormal_with_positive_determinant(self):
        matrix = evaluate_forward_kinematics(sample())["joints"][1]["rotation_matrix"]
        for i in range(3):
            for j in range(3):
                dot = sum(matrix[k][i] * matrix[k][j] for k in range(3))
                self.assertAlmostEqual(dot, 1.0 if i == j else 0.0, places=10)
        determinant = (
            matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
            - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
            + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
        )
        self.assertAlmostEqual(determinant, 1.0, places=10)

    def test_axis_is_normalized(self):
        rig = {"schema": "mojum.virtual-rig/1", "visibility": "PUBLIC_SYNTHETIC",
               "root": "r", "joints": [joint("r", None, axis=(0, 0, 5), angle=0.5)]}
        normal = evaluate_forward_kinematics(rig)
        rig["joints"][0]["axis"] = [0, 0, 1]
        self.assertEqual(normal, evaluate_forward_kinematics(rig))

    def test_nonzero_axis_required(self):
        rig = sample()
        rig["joints"][0]["axis"] = [0, 0, 0]
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_angle_limits_are_enforced(self):
        for field, value in (("angle_rad", 4.0), ("min_rad", 3.0), ("max_rad", -3.0)):
            rig = sample()
            rig["joints"][1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                evaluate_forward_kinematics(rig)

    def test_parent_must_precede_child(self):
        rig = sample()
        rig["joints"][1]["parent"] = "hand"
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_single_declared_root_required(self):
        rig = sample()
        rig["joints"][1]["parent"] = None
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_duplicate_ids_rejected(self):
        rig = sample()
        rig["joints"][2]["id"] = "spine"
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_root_id_must_match_first_joint(self):
        rig = sample()
        rig["root"] = "hand"
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_unknown_fields_rejected(self):
        rig = sample()
        rig["private_notes"] = "not part of the contract"
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)
        rig = sample()
        rig["joints"][0]["motor_command"] = 1
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_only_public_synthetic_inputs_admitted(self):
        for key, value in (("visibility", "PRIVATE"), ("visibility", "PUBLIC"),
                           ("schema", "mojum.virtual-rig/2")):
            rig = sample()
            rig[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                evaluate_forward_kinematics(rig)

    def test_boolean_and_nonfinite_numbers_rejected(self):
        for bad in (True, float("nan"), float("inf"), -float("inf"), 10**1000):
            rig = sample()
            rig["joints"][0]["origin_m"][0] = bad
            with self.subTest(value=type(bad).__name__), self.assertRaises(ValueError):
                evaluate_forward_kinematics(rig)

    def test_malformed_vectors_rejected(self):
        for bad in ([0, 1], [0, 1, 2, 3], (0, 0, 0), "0,0,0", [0, 0, None]):
            rig = sample()
            rig["joints"][0]["axis"] = bad
            with self.subTest(value=bad), self.assertRaises(ValueError):
                evaluate_forward_kinematics(rig)

    def test_empty_and_oversized_rigs_rejected(self):
        rig = sample()
        rig["joints"] = []
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)
        rig = sample()
        rig["joints"] = [joint(f"j{i}", None if i == 0 else f"j{i-1}")
                         for i in range(129)]
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)

    def test_input_does_not_mutate_and_output_is_detached(self):
        rig = sample()
        original = copy.deepcopy(rig)
        report = evaluate_forward_kinematics(rig)
        self.assertEqual(rig, original)
        rig["joints"][0]["origin_m"][0] = 999
        self.assertEqual(report["joints"][0]["position_m"], [0.0, 0.0, 0.0])
        self.assertFalse(report["physical_actuation"])
        self.assertFalse(report["physical_contact"])

    def test_report_digest_and_tamper_detection(self):
        report = evaluate_forward_kinematics(sample())
        self.assertTrue(verify_kinematics_report(report))
        report["joints"][1]["position_m"][0] += 0.1
        self.assertFalse(verify_kinematics_report(report))

    def test_report_digest_is_deterministic(self):
        self.assertEqual(evaluate_forward_kinematics(sample()),
                         evaluate_forward_kinematics(sample()))

    def test_invalid_report_shapes_fail_closed(self):
        for bad in (None, [], {}, {"sha256": "bad"}, {"sha256": "0" * 64, "x": object()}):
            self.assertFalse(verify_kinematics_report(bad))

    def test_verifier_rejects_nested_mapping_and_sequence_subclasses(self):
        class HostileDict(dict):
            def items(self):
                raise AssertionError("untrusted items() must never run")

        class HostileList(list):
            def __iter__(self):
                raise AssertionError("untrusted __iter__ must never run")

        for nested in (HostileDict(value=1), HostileList([1, 2])):
            with self.subTest(nested_type=type(nested).__name__):
                report = evaluate_forward_kinematics(sample())
                report["untrusted"] = nested
                self.assertFalse(verify_kinematics_report(report))

    def test_verifier_rejects_cycles_depth_and_oversized_text(self):
        report = evaluate_forward_kinematics(sample())
        cyclic = dict(report)
        cyclic["cycle"] = cyclic
        self.assertFalse(verify_kinematics_report(cyclic))

        too_deep = dict(report)
        cursor = too_deep
        for _ in range(18):
            child = {}
            cursor["nested"] = child
            cursor = child
        self.assertFalse(verify_kinematics_report(too_deep))

        oversized = dict(report)
        oversized["extra"] = "x" * 262145
        self.assertFalse(verify_kinematics_report(oversized))


    def test_rig_and_joint_mapping_subclasses_rejected(self):
        class Rig(dict):
            pass
        class Joint(dict):
            pass
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(Rig(sample()))
        rig = sample()
        rig["joints"][0] = Joint(rig["joints"][0])
        with self.assertRaises(ValueError):
            evaluate_forward_kinematics(rig)


if __name__ == "__main__":
    unittest.main()
