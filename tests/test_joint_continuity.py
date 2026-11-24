import math
import unittest
from mojum.joint_continuity import audit_joint_continuity

def s(t, p, r="rig-r1"): return {"tick": t, "revision": r, "positions": p}

class JointContinuityTests(unittest.TestCase):
    def test_valid_and_deterministic(self):
        rows=[s(10,{"hip":0.0,"knee":0.1}),s(11,{"hip":0.2,"knee":0.2})]
        a=audit_joint_continuity(rows,{"hip":0.25,"knee":0.2},expected_revision="rig-r1")
        b=audit_joint_continuity(rows,{"hip":0.25,"knee":0.2},expected_revision="rig-r1")
        self.assertTrue(a["ok"]); self.assertEqual(a["digest_sha256"],b["digest_sha256"])
    def test_threshold_equality(self):
        self.assertTrue(audit_joint_continuity([s(0,{"j":0}),s(1,{"j":.5})],{"j":.5})["ok"])
    def test_exceedance_first_tick(self):
        r=audit_joint_continuity([s(0,{"j":0}),s(1,{"j":.4}),s(2,{"j":1})],{"j":.5})
        self.assertFalse(r["ok"]); self.assertEqual(r["violations"][0]["first_offending_tick"],2)
    def test_wrap_is_explicit(self):
        rows=[s(0,{"yaw":math.pi-.05}),s(1,{"yaw":-math.pi+.05})]
        self.assertFalse(audit_joint_continuity(rows,{"yaw":.2})["ok"])
        self.assertTrue(audit_joint_continuity(rows,{"yaw":.2},revolute=("yaw",))["ok"])
    def test_tick_gap_and_bool_tick(self):
        for rows in ([s(0,{"j":0}),s(2,{"j":0})],[s(True,{"j":0})]):
            with self.assertRaises(ValueError): audit_joint_continuity(rows,{"j":1})
    def test_revision_and_joint_set_changes(self):
        for rows in ([s(0,{"j":0}),s(1,{"j":0},"r2")],[s(0,{"j":0}),s(1,{"k":0})]):
            with self.assertRaises(ValueError): audit_joint_continuity(rows,{"j":1})
    def test_bool_and_nonfinite_positions(self):
        for v in (True,float("nan"),float("inf")):
            with self.assertRaises(ValueError): audit_joint_continuity([s(0,{"j":v})],{"j":1})
    def test_limits_and_revolute_validation(self):
        for limits in ({"j":0},{"j":True},{"j":float("nan")},{"j":7}):
            with self.assertRaises(ValueError): audit_joint_continuity([s(0,{"j":0})],limits)
        for rev in (("other",),("j","j")):
            with self.assertRaises(ValueError): audit_joint_continuity([s(0,{"j":0})],{"j":1},revolute=rev)
    def test_bounds_and_schema(self):
        with self.assertRaises(ValueError): audit_joint_continuity([s(0,{"j":0}),s(1,{"j":0})],{"j":1},max_samples=1)
        with self.assertRaises(ValueError): audit_joint_continuity([s(0,{"j":0,"k":0})],{"j":1,"k":1},max_joints=1)
        with self.assertRaises(ValueError): audit_joint_continuity([{"tick":0,"revision":"r","positions":{"j":0},"extra":1}],{"j":1})
        with self.assertRaises(ValueError): audit_joint_continuity([],{"j":1})

if __name__ == "__main__": unittest.main()
