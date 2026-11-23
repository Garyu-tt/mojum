import dataclasses
import unittest
from mojum.vrm_pose_interpolation import PoseInterpolationError, interpolate_measured_poses, verify_interpolated_pose

def frame(frame_id, timestamp, x, *, epoch="body-1", revision=3, classification="PUBLIC_SYNTHETIC"):
    return {"schema":"mojum.vrm-measured-pose/1","classification":classification,"frame_id":frame_id,
            "body_epoch":epoch,"rig_revision":revision,"coordinate_system":"METER_RIGHT_HANDED_Y_UP",
            "timestamp_ns":timestamp,"joints":{"root":(x,0.0,0.0),"hand.left":(x,1.0,0.0)}}

class PoseInterpolationTests(unittest.TestCase):
    def test_midpoint_and_digest_are_deterministic(self):
        a,b=frame("f-a",100,0.0),frame("f-b",200,1.0)
        result=interpolate_measured_poses(a,b,target_timestamp_ns=150)
        self.assertEqual(result.joint_names,("hand.left","root"))
        self.assertEqual(result.coordinates,((0.5,1.0,0.0),(0.5,0.0,0.0)))
        self.assertTrue(verify_interpolated_pose(result))
        self.assertEqual(result.digest,interpolate_measured_poses(a,b,target_timestamp_ns=150).digest)
    def test_endpoints_and_no_extrapolation(self):
        a,b=frame("f-a",100,0.0),frame("f-b",200,1.0)
        self.assertEqual(interpolate_measured_poses(a,b,target_timestamp_ns=100).ratio,0.0)
        self.assertEqual(interpolate_measured_poses(a,b,target_timestamp_ns=200).ratio,1.0)
        for t in (99,201):
            with self.assertRaises(PoseInterpolationError): interpolate_measured_poses(a,b,target_timestamp_ns=t)
    def test_rejects_mixed_epoch_revision_and_space(self):
        a,b=frame("f-a",100,0.0),frame("f-b",200,1.0)
        for changed in (frame("f-b",200,1.0,epoch="body-2"),frame("f-b",200,1.0,revision=4)):
            with self.assertRaises(PoseInterpolationError): interpolate_measured_poses(a,changed,target_timestamp_ns=150)
        changed=frame("f-b",200,1.0); changed["coordinate_system"]="LEFT_HANDED"
        with self.assertRaises(PoseInterpolationError): interpolate_measured_poses(a,changed,target_timestamp_ns=150)
    def test_rejects_time_order_and_inventory_mismatch(self):
        with self.assertRaises(PoseInterpolationError):
            interpolate_measured_poses(frame("f-a",200,0.0),frame("f-b",100,1.0),target_timestamp_ns=150)
        a,b=frame("f-a",100,0.0),frame("f-b",200,1.0); del b["joints"]["hand.left"]
        with self.assertRaises(PoseInterpolationError): interpolate_measured_poses(a,b,target_timestamp_ns=150)
    def test_rejects_discontinuity(self):
        with self.assertRaises(PoseInterpolationError):
            interpolate_measured_poses(frame("f-a",100,0.0),frame("f-b",200,3.0),target_timestamp_ns=150,max_joint_delta=2.0)
    def test_rejects_bool_nan_inf_and_strings(self):
        for value in (True,float("nan"),float("inf"),"1"):
            a,b=frame("f-a",100,0.0),frame("f-b",200,1.0); a["joints"]["root"]=(value,0.0,0.0)
            with self.subTest(value=repr(value)),self.assertRaises(PoseInterpolationError):
                interpolate_measured_poses(a,b,target_timestamp_ns=150)
    def test_rejects_private_and_bool_timestamp(self):
        with self.assertRaises(PoseInterpolationError):
            interpolate_measured_poses(frame("f-a",100,0.0),frame("f-b",200,1.0,classification="PRIVATE"),target_timestamp_ns=150)
        with self.assertRaises(PoseInterpolationError):
            interpolate_measured_poses(frame("f-a",True,0.0),frame("f-b",200,1.0),target_timestamp_ns=150)
    def test_detaches_inputs_and_detects_tampering(self):
        a,b=frame("f-a",100,0.0),frame("f-b",200,1.0)
        result=interpolate_measured_poses(a,b,target_timestamp_ns=150); a["joints"]["root"]=(99.0,0.0,0.0)
        self.assertEqual(result.coordinates[-1],(0.5,0.0,0.0))
        self.assertFalse(verify_interpolated_pose(dataclasses.replace(result,rig_revision=9)))
    def test_rejects_duplicate_ids_and_extra_fields(self):
        a,b=frame("f-a",100,0.0),frame("f-a",200,1.0)
        with self.assertRaises(PoseInterpolationError): interpolate_measured_poses(a,b,target_timestamp_ns=150)
        a["extra"]="no"
        with self.assertRaises(PoseInterpolationError): interpolate_measured_poses(a,frame("f-b",200,1.0),target_timestamp_ns=150)

if __name__ == "__main__": unittest.main()
