import test from 'node:test';
import assert from 'node:assert/strict';
import {createVrmPoseTimeline} from '../tools/vrm_pose_timeline.mjs';

const revision = 'a'.repeat(64);
function frame(sequence, options = {}) {
  return {
    schema: 'mojum.vrm-pose-timeline/1',
    classification: 'PUBLIC_SYNTHETIC',
    sequence,
    epoch: 'body-epoch-01',
    tick: sequence * 2,
    simulation_time_ns: sequence * 20_000_000,
    revision,
    joints: [
      {id: 'hip.yaw', angle_rad: sequence / 10},
      {id: 'neck.pitch', angle_rad: -sequence / 20},
    ],
    ...options,
  };
}

test('seek, step in both directions and rewind are deterministic', () => {
  const timeline = createVrmPoseTimeline([frame(10), frame(11), frame(12)]);
  assert.equal(timeline.length, 3);
  assert.equal(timeline.position(), 0);
  assert.equal(timeline.current().sequence, 10);
  assert.equal(timeline.step().sequence, 11);
  assert.equal(timeline.step(1).sequence, 12);
  assert.equal(timeline.step(-1).sequence, 11);
  assert.equal(timeline.seek(2).sequence, 12);
  assert.equal(timeline.frameAt(0).sequence, 10);
  assert.equal(timeline.position(), 0);
  assert.equal(timeline.step().sequence, 11);
  assert.equal(timeline.rewind().sequence, 10);
  assert.equal(timeline.position(), 0);
});

test('joint canonical order is locale-independent ASCII code-point order', () => {
  const mixedCase = frame(0, {
    joints: [
      {id: 'a', angle_rad: 0.1},
      {id: 'A', angle_rad: 0.2},
      {id: 'B', angle_rad: 0.3},
      {id: 'b', angle_rad: 0.4},
    ],
  });
  const timeline = createVrmPoseTimeline([mixedCase]);
  assert.deepEqual(timeline.current().joints.map(joint => joint.id),
    ['A', 'B', 'a', 'b']);
});

test('frame and joint data are detached and immutable', () => {
  const first = frame(0);
  const timeline = createVrmPoseTimeline([first, frame(1)]);
  first.joints[0].angle_rad = 2.5;
  first.epoch = 'mutated';
  assert.equal(timeline.current().epoch, 'body-epoch-01');
  assert.equal(timeline.current().joints[0].angle_rad, 0);
  assert.equal(Object.isFrozen(timeline.current()), true);
  assert.equal(Object.isFrozen(timeline.current().joints), true);
  assert.equal(Object.isFrozen(timeline.current().joints[0]), true);
  assert.throws(() => { timeline.current().tick = 99; }, TypeError);
});

test('rejects gaps, duplicate/reordered sequence and nonmonotonic time', () => {
  assert.throws(() => createVrmPoseTimeline([frame(0), frame(2)]), /contiguous/);
  assert.throws(() => createVrmPoseTimeline([frame(0), frame(0)]), /contiguous/);
  assert.throws(() => createVrmPoseTimeline([frame(1), frame(0)]), /contiguous/);
  const repeatedTick = frame(1);
  repeatedTick.tick = 0;
  assert.throws(() => createVrmPoseTimeline([frame(0), repeatedTick]), /contiguous/);
  const repeatedTime = frame(1);
  repeatedTime.simulation_time_ns = 0;
  assert.throws(() => createVrmPoseTimeline([frame(0), repeatedTime]), /contiguous/);
});

test('rejects cross-epoch frames and changing joint inventory', () => {
  const otherEpoch = frame(1, {epoch: 'body-epoch-02'});
  assert.throws(() => createVrmPoseTimeline([frame(0), otherEpoch]), /epochs/);
  const otherRevision = frame(1, {revision: 'b'.repeat(64)});
  assert.throws(() => createVrmPoseTimeline([frame(0), otherRevision]), /revisions/);
  const otherJoints = frame(1, {
    joints: [{id: 'hip.yaw', angle_rad: 0.1}],
  });
  assert.throws(() => createVrmPoseTimeline([frame(0), otherJoints]), /inventory/);
});

test('rejects malformed schema, hostile field sets, values and bounds', () => {
  assert.throws(() => createVrmPoseTimeline([]), /1\.\.10000/);
  assert.throws(() => createVrmPoseTimeline(Array(10001).fill(frame(0))), /1\.\.10000/);
  assert.throws(() => createVrmPoseTimeline([null]), /plain record/);
  assert.throws(() => createVrmPoseTimeline([{...frame(0), extra: true}]), /field set/);
  assert.throws(() => createVrmPoseTimeline([{...frame(0), sequence: true}]), /metadata/);
  assert.throws(() => createVrmPoseTimeline([{...frame(0), revision: 'x'}]), /metadata/);
  assert.throws(() => createVrmPoseTimeline([{...frame(0), classification: 'PRIVATE'}]), /metadata/);
  const getterFrame = frame(0);
  let getterCalled = false;
  Object.defineProperty(getterFrame, 'epoch', {enumerable: true, get() {
    getterCalled = true;
    return 'body-epoch-01';
  }});
  assert.throws(() => createVrmPoseTimeline([getterFrame]), /data properties/);
  assert.equal(getterCalled, false);
  const sparseJoints = frame(0);
  sparseJoints.joints = Array(1);
  assert.throws(() => createVrmPoseTimeline([sparseJoints]), /dense array/);
  const badAngle = frame(0);
  badAngle.joints[1].angle_rad = Number.NaN;
  assert.throws(() => createVrmPoseTimeline([badAngle]), /joint/);
  const duplicateJoint = frame(0);
  duplicateJoint.joints[1].id = 'hip.yaw';
  assert.throws(() => createVrmPoseTimeline([duplicateJoint]), /joint/);
  assert.throws(() => createVrmPoseTimeline([frame(0)]).seek(-1), RangeError);
  assert.throws(() => createVrmPoseTimeline([frame(0)]).seek(1), RangeError);
  assert.throws(() => createVrmPoseTimeline([frame(0)]).step(0), /direction/);
  assert.throws(() => createVrmPoseTimeline([frame(0)]).step(-1), /boundary/);
  assert.throws(() => createVrmPoseTimeline([frame(0)]).step(1), /boundary/);
});
