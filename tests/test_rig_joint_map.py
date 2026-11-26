import unittest
from mojum.rig_joint_map import JointMapError, SCHEMA, build_joint_map


def src(joints=None):
    return {"rig_id": "source-rig", "revision": "rev-1",
            "joints": ["hip", "left_knee", "right_knee"] if joints is None else joints}


def dst(ids=None):
    return {"rig_id": "mojum-dante", "revision": "skeleton-v1",
            "joint_ids": ["pelvis", "knee.L", "knee.R"] if ids is None else ids}


def links():
    return {"pelvis": "hip", "knee.L": "left_knee", "knee.R": "right_knee"}


class HostileString(str):
    def __hash__(self):
        return str.__hash__(self)
    def __eq__(self, other):
        raise RuntimeError("hostile equality called")


class JointMapTests(unittest.TestCase):
    def test_complete_map_preserves_target_order(self):
        result = build_joint_map(src(), dst(), links())
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual([(b.joint_id, b.source_name) for b in result.bindings],
                         [("pelvis", "hip"), ("knee.L", "left_knee"), ("knee.R", "right_knee")])
        self.assertEqual(len(result.digest), 64)

    def test_deterministic_and_revision_pinned(self):
        first = build_joint_map(src(), dst(), links())
        self.assertEqual(first.digest, build_joint_map(src(), dst(), links()).digest)
        changed = src()
        changed["revision"] = "rev-2"
        self.assertNotEqual(first.digest, build_joint_map(changed, dst(), links()).digest)

    def test_unused_source_joints_are_allowed(self):
        self.assertEqual(len(build_joint_map(src(["hip", "left_knee", "right_knee", "jaw"]),
                                             dst(), links()).bindings), 3)

    def test_explicit_alias_is_visible(self):
        result = build_joint_map(src(), dst(), {"pelvis": "root", "knee.L": "left_knee",
                                               "knee.R": "right_knee"}, {"root": "hip"})
        self.assertTrue(result.bindings[0].via_alias)
        self.assertEqual(result.bindings[0].source_name, "hip")

    def test_alias_is_not_fuzzy_or_chained(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), links(), {"hip_alias": "missing"})
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), links(), {"a": "hip", "b": "a"})

    def test_alias_cannot_shadow_real_name(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), links(), {"hip": "left_knee"})

    def test_detached_serialization(self):
        result = build_joint_map(src(), dst(), links())
        exported = result.as_dict()
        exported["bindings"][0]["source_name"] = "forged"
        self.assertEqual(result.bindings[0].source_name, "hip")

    def test_binding_records_are_frozen(self):
        result = build_joint_map(src(), dst(), links())
        with self.assertRaises((AttributeError, TypeError)):
            result.bindings[0].source_name = "forged"

    def test_missing_target_binding(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), {"pelvis": "hip"})

    def test_unknown_target_binding(self):
        bad = links()
        bad["unexpected"] = "jaw"
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), bad)

    def test_duplicate_source_binding(self):
        bad = links()
        bad["knee.L"] = "hip"
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), bad)

    def test_unknown_source_binding(self):
        bad = links()
        bad["knee.R"] = "right_arm"
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), bad)

    def test_duplicate_inventories(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(["hip", "hip"]), dst(), links())
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(["pelvis", "pelvis"]), links())

    def test_invalid_stable_identifier(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(["not valid"]), {"not valid": "hip"})

    def test_unknown_descriptor_fields(self):
        bad = src()
        bad["extra"] = "unexpected"
        with self.assertRaises(JointMapError):
            build_joint_map(bad, dst(), links())

    def test_bad_revision_rejected(self):
        bad = dst()
        bad["revision"] = ""
        with self.assertRaises(JointMapError):
            build_joint_map(src(), bad, links())

    def test_non_ascii_revision_rejected_by_identifier_contract(self):
        bad = dst()
        bad["revision"] = "skeleton-версия"
        with self.assertRaises(JointMapError):
            build_joint_map(src(), bad, links())

    def test_hostile_binding_value_rejected(self):
        bad = links()
        bad["knee.R"] = HostileString("right_knee")
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), bad)

    def test_hostile_binding_key_rejected_before_equality(self):
        bad = {HostileString("pelvis"): "hip", "knee.L": "left_knee", "knee.R": "right_knee"}
        with self.assertRaises(JointMapError):
            build_joint_map(src(), dst(), bad)

    def test_container_subclasses_rejected(self):
        class DictSubclass(dict):
            pass
        class ListSubclass(list):
            pass
        with self.assertRaises(JointMapError):
            build_joint_map(DictSubclass(src()), dst(), links())
        bad = src()
        bad["joints"] = ListSubclass(bad["joints"])
        with self.assertRaises(JointMapError):
            build_joint_map(bad, dst(), links())

    def test_inventory_budget(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(["j" + str(i) for i in range(257)]), dst(), links())

    def test_unicode_c1_control_characters_rejected(self):
        # C1 controls are Unicode category Cc even though they are above ASCII DEL.
        for control in ("\u0085", "\u009f"):
            with self.subTest(control=hex(ord(control))):
                with self.assertRaises(JointMapError):
                    build_joint_map(src(["hip" + control]), dst(), links())
                bad_revision = dst()
                bad_revision["revision"] = "rev" + control
                with self.assertRaises(JointMapError):
                    build_joint_map(src(), bad_revision, links())

    def test_invalid_utf8_surrogate_rejected(self):
        with self.assertRaises(JointMapError):
            build_joint_map(src(["hip\\ud800"]), dst(), links())

    def test_canonical_receipt_is_json_serializable(self):
        import json
        result = build_joint_map(src(), dst(), links())
        encoded = json.dumps(result.as_dict(), sort_keys=True)
        self.assertIn(result.digest, encoded)


if __name__ == "__main__":
    unittest.main()
