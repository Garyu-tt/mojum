"""Opt-in browser/Node verification; stdlib tests still run without Node."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "optional Node.js not installed")
class VRMPoseProjectorNodeTests(unittest.TestCase):
    def test_strict_visual_projection_replay_and_refusal(self):
        uri = (ROOT / "tools" / "vrm_pose_projector.mjs").as_uri()
        js = """import assert from 'node:assert/strict';
import { createVrmPoseProjector } from %s;

const hip = {rotation: {x: 0, y: 0, z: 0}};
const head = {rotation: {x: 0, y: 0, z: 0}};
const bones = {hips: hip, head: head};
const vrm = {humanoid: {getNormalizedBoneNode: name => bones[name] ?? null}};
const projector = createVrmPoseProjector(vrm, [
  {id:'hip.yaw', bone:'hips',axis:'y'},
  {id:'neck.pitch',bone:'head',axis:'x'},
]);
const packet = (tick, a = 0.2) => ({
  schema:'mojum.vrm-measured-pose/1', tick,
  simulation_time_ns: tick * 10000000, revision:'a'.repeat(64),
  joints:[{id:'hip.yaw',angle_rad:a},{id:'neck.pitch',angle_rad:-0.3}],
});
const out = projector.apply(packet(2));
assert.equal(out.applied_joints, 2);
assert.equal(out.visual_only, true);
assert.equal(hip.rotation.y, 0.2);
assert.equal(head.rotation.x, -0.3);
assert.throws(() => projector.apply(packet(2)));
assert.equal(hip.rotation.y, 0.2);
const partial = packet(3);
partial.joints.pop();
assert.throws(() => projector.apply(partial));
assert.equal(head.rotation.x, -0.3);
const invalid = packet(3);
invalid.joints[0].angle_rad = Number.NaN;
assert.throws(() => projector.apply(invalid));
assert.equal(hip.rotation.y, 0.2);
const unexpected = packet(3);
unexpected.joints[0].id = 'unknown';
assert.throws(() => projector.apply(unexpected));
assert.equal(hip.rotation.y, 0.2);
assert.throws(() => createVrmPoseProjector(vrm, [
  {id:'a',bone:'hips',axis:'y'}, {id:'b',bone:'hips',axis:'y'},
]));
assert.throws(() => createVrmPoseProjector(vrm, [
  {id:'a',bone:'unknown',axis:'y'},
]));
assert.equal(projector.apply(packet(3, -0.1)).tick, 3);
assert.equal(hip.rotation.y, -0.1);
projector.resetReplay();
assert.equal(hip.rotation.y, 0);
assert.equal(head.rotation.x, 0);
assert.equal(projector.apply(packet(0, 0.1)).tick, 0);
assert.equal(hip.rotation.y, 0.1);

// A loaded avatar can carry nonzero initial normalized rotations. Replaying
// absolute measurements must not irreversibly erase that original rest pose.
const tiltedHead = {rotation: {x: 0.35, y: -0.15, z: 0.4}};
const tiltedVrm = {humanoid: {getNormalizedBoneNode: name =>
  name === 'head' ? tiltedHead : null}};
const second = createVrmPoseProjector(tiltedVrm, [
  {id:'neck.pitch',bone:'head',axis:'x'},
]);
second.apply({
  schema:'mojum.vrm-measured-pose/1', tick:1,
  simulation_time_ns:10000000, revision:'a'.repeat(64),
  joints:[{id:'neck.pitch',angle_rad:-0.25}],
});
assert.equal(tiltedHead.rotation.x, -0.25);
second.resetReplay();
assert.equal(tiltedHead.rotation.x, 0.35, 'replay reset erased loaded rest pose');
assert.equal(tiltedHead.rotation.y, -0.15);
assert.equal(tiltedHead.rotation.z, 0.4);
assert.equal(second.apply({
  schema:'mojum.vrm-measured-pose/1', tick:0,
  simulation_time_ns:0, revision:'a'.repeat(64),
  joints:[{id:'neck.pitch',angle_rad:0.1}],
}).tick, 0);
""" % json.dumps(uri)
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", js],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
