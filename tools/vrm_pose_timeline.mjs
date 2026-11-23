/**
 * Deterministic cursor over already-measured synthetic VRM pose frames.
 *
 * This module never interpolates, invents samples, or touches a renderer.
 * It is an offline playback index only.
 */
const SCHEMA = 'mojum.vrm-pose-timeline/1';
const REVISION = /^[0-9a-f]{64}$/;
const EPOCH = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
const JOINT_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
const MAX_FRAMES = 10000;
const MAX_JOINTS = 128;
const MAX_JOINT_SAMPLES = 256_000;

function exactRecord(value, keys, name) {
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      (Object.getPrototypeOf(value) !== Object.prototype &&
       Object.getPrototypeOf(value) !== null)) {
    throw new TypeError(name + ' must be a plain record');
  }
  const actual = Reflect.ownKeys(value);
  if (actual.some(key => typeof key !== 'string') ||
      actual.length !== keys.length ||
      actual.slice().sort().join(',') !== keys.slice().sort().join(',')) {
    throw new TypeError(name + ' has an invalid field set');
  }
  for (const key of actual) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !Object.hasOwn(descriptor, 'value') || !descriptor.enumerable) {
      throw new TypeError(name + ' fields must be enumerable data properties');
    }
  }
}

function exactArray(value, minimum, maximum, name) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype) {
    throw new TypeError(name + ' has an invalid array shape');
  }
  if (value.length < minimum || value.length > maximum) {
    throw new TypeError(name + ' must contain ' + minimum + '..' + maximum + ' entries');
  }
  const keys = Reflect.ownKeys(value);
  if (keys.length !== value.length + 1 || !keys.includes('length')) {
    throw new TypeError(name + ' must be a dense array without extra fields');
  }
  for (let i = 0; i < value.length; i += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(i));
    if (!descriptor || !Object.hasOwn(descriptor, 'value') || !descriptor.enumerable) {
      throw new TypeError(name + ' must contain only data elements');
    }
  }
  return value;
}

function cloneFrame(frame, expectedJointIds) {
  exactRecord(frame,
    ['schema', 'classification', 'sequence', 'epoch', 'tick', 'simulation_time_ns',
     'revision', 'joints'], 'frame');
  if (frame.schema !== SCHEMA || frame.classification !== 'PUBLIC_SYNTHETIC' ||
      !Number.isSafeInteger(frame.sequence) || frame.sequence < 0 ||
      !Number.isSafeInteger(frame.tick) || frame.tick < 0 ||
      !Number.isSafeInteger(frame.simulation_time_ns) ||
      frame.simulation_time_ns < 0 ||
      typeof frame.epoch !== 'string' || !EPOCH.test(frame.epoch) ||
      typeof frame.revision !== 'string' || !REVISION.test(frame.revision)) {
    throw new TypeError('invalid VRM timeline frame metadata');
  }
  exactArray(frame.joints, 1, MAX_JOINTS, 'joints');

  const joints = [];
  const ids = new Set();
  for (const joint of frame.joints) {
    exactRecord(joint, ['id', 'angle_rad'], 'joint');
    if (typeof joint.id !== 'string' || !JOINT_ID.test(joint.id) ||
        ids.has(joint.id) || typeof joint.angle_rad !== 'number' ||
        !Number.isFinite(joint.angle_rad) ||
        Math.abs(joint.angle_rad) > Math.PI) {
      throw new TypeError('invalid or duplicate measured joint');
    }
    ids.add(joint.id);
    joints.push(Object.freeze({id: joint.id, angle_rad: joint.angle_rad}));
  }
  // ASCII-only IDs use locale-independent code-point ordering.
  joints.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const canonicalIds = joints.map(joint => joint.id);
  if (expectedJointIds &&
      (canonicalIds.length !== expectedJointIds.length ||
       canonicalIds.some((id, i) => id !== expectedJointIds[i]))) {
    throw new TypeError('joint inventory changed inside a timeline');
  }

  return Object.freeze({
    schema: SCHEMA,
    classification: 'PUBLIC_SYNTHETIC',
    sequence: frame.sequence,
    epoch: frame.epoch,
    tick: frame.tick,
    simulation_time_ns: frame.simulation_time_ns,
    revision: frame.revision,
    joints: Object.freeze(joints),
  });
}

/**
 * Build an immutable timeline from a bounded sequence of measured frames.
 * Sequence numbers must be contiguous; logical time/tick must strictly rise.
 */
export function createVrmPoseTimeline(inputFrames) {
  exactArray(inputFrames, 1, MAX_FRAMES, 'frames');

  const frames = [];
  let expectedJointIds = null;
  let firstEpoch = null;
  let firstRevision = null;
  let lastSequence = null;
  let lastTick = null;
  let lastTime = null;
  let totalJointSamples = 0;

  for (const input of inputFrames) {
    const frame = cloneFrame(input, expectedJointIds);
    totalJointSamples += frame.joints.length;
    if (totalJointSamples > MAX_JOINT_SAMPLES) {
      throw new RangeError('timeline exceeds ' + MAX_JOINT_SAMPLES + ' joint samples');
    }
    if (firstEpoch === null) {
      firstEpoch = frame.epoch;
      firstRevision = frame.revision;
      expectedJointIds = frame.joints.map(joint => joint.id);
    } else if (frame.epoch !== firstEpoch) {
      throw new TypeError('timeline cannot mix body epochs');
    } else if (frame.revision !== firstRevision) {
      throw new TypeError('timeline cannot mix body revisions');
    }
    if (lastSequence !== null &&
        (frame.sequence !== lastSequence + 1 ||
         frame.tick <= lastTick ||
         frame.simulation_time_ns <= lastTime)) {
      throw new TypeError('frames must be contiguous and strictly time-ordered');
    }
    frames.push(frame);
    lastSequence = frame.sequence;
    lastTick = frame.tick;
    lastTime = frame.simulation_time_ns;
  }

  let cursor = 0;
  function seek(index) {
    if (!Number.isSafeInteger(index) || index < 0 || index >= frames.length) {
      throw new RangeError('frame index is outside the timeline');
    }
    cursor = index;
    return frames[cursor];
  }
  function step(direction = 1) {
    if (direction !== 1 && direction !== -1) {
      throw new TypeError('step direction must be exactly 1 or -1');
    }
    const next = cursor + direction;
    if (next < 0 || next >= frames.length) {
      throw new RangeError('timeline boundary reached');
    }
    cursor = next;
    return frames[cursor];
  }
  function rewind() {
    cursor = 0;
    return frames[cursor];
  }
  function current() {
    return frames[cursor];
  }

  return Object.freeze({
    length: frames.length,
    current,
    seek,
    step,
    rewind,
    frameAt: seek,
    position: () => cursor,
  });
}
