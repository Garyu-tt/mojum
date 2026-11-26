/**
 * Read-only, opt-in adapter from a synthetic measured pose to VRM humanoid bones.
 * No animation player, simulation writes, network, or third-party imports.
 */
export const POSE_SCHEMA = 'mojum.vrm-pose/1';
export const MAPPING_SCHEMA = 'mojum.vrm-bone-map/1';
const MAX_BONES = 55;
const MAX_TICK = Number.MAX_SAFE_INTEGER;
const BONE_NAMES = new Set([
  'hips','spine','chest','upperChest','neck','head',
  'leftEye','rightEye','jaw',
  'leftShoulder','leftUpperArm','leftLowerArm','leftHand',
  'rightShoulder','rightUpperArm','rightLowerArm','rightHand',
  'leftUpperLeg','leftLowerLeg','leftFoot','leftToes',
  'rightUpperLeg','rightLowerLeg','rightFoot','rightToes',
  ...['left','right'].flatMap(side => [
    'ThumbMetacarpal','ThumbProximal','ThumbDistal',
    'IndexProximal','IndexIntermediate','IndexDistal',
    'MiddleProximal','MiddleIntermediate','MiddleDistal',
    'RingProximal','RingIntermediate','RingDistal',
    'LittleProximal','LittleIntermediate','LittleDistal',
  ].map(suffix => side + suffix))
]);

function object(value, keys, where) {
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new TypeError(`${where}: expected plain object`);
  }
  if (Object.keys(value).some(key => !keys.includes(key))) {
    throw new TypeError(`${where}: unexpected field`);
  }
  return value;
}
function exactly(value, keys, where) {
  object(value, keys, where);
  if (Object.keys(value).length !== keys.length ||
      keys.some(key => !Object.hasOwn(value, key))) {
    throw new TypeError(`${where}: missing or extra field`);
  }
  return value;
}
function vector(value, n, limit, where) {
  if (!Array.isArray(value) || value.length !== n ||
      !value.every(x => typeof x === 'number' && Number.isFinite(x) && Math.abs(x) <= limit)) {
    throw new TypeError(`${where}: invalid vector`);
  }
  return [...value];
}
function rotation(value, where) {
  const q = vector(value, 4, 1, where);
  const norm = q.reduce((s, x) => s + x * x, 0);
  if (Math.abs(norm - 1) > 1e-4) throw new RangeError(`${where}: quaternion not normalized`);
  return q;
}
function revision(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(value)) {
    throw new TypeError('invalid source revision');
  }
  return value;
}

/** An exact, explicit map from source joint names to normalized VRM bone IDs. */
export function validateBoneMap(input) {
  exactly(input, ['schema', 'joints'], 'bone map');
  if (input.schema !== MAPPING_SCHEMA) throw new TypeError('unsupported bone map schema');
  const joints = object(input.joints, Object.keys(input.joints), 'joints');
  const keys = Object.keys(joints);
  if (keys.length < 1 || keys.length > MAX_BONES) throw new RangeError('invalid joint count');
  const targets = new Set();
  for (const key of keys) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key)) throw new TypeError('unsafe joint id');
    const target = joints[key];
    if (!BONE_NAMES.has(target) || targets.has(target)) throw new TypeError('unknown or duplicate VRM bone');
    targets.add(target);
  }
  return Object.freeze(Object.fromEntries(keys.map(key => [key, joints[key]])));
}

/** Validate an entire pose before touching any VRM object. Reject hidden extras. */
export function validatePose(frame, map) {
  exactly(frame, ['schema', 'tick', 'source_revision', 'coordinate_space', 'root_meters', 'joints'], 'pose');
  if (frame.schema !== POSE_SCHEMA || frame.coordinate_space !== 'vrm-normalized-local') {
    throw new TypeError('unsupported pose schema or coordinate space');
  }
  if (!Number.isSafeInteger(frame.tick) || frame.tick < 0 || frame.tick > MAX_TICK) {
    throw new RangeError('invalid tick');
  }
  const source_revision = revision(frame.source_revision);
  const root_meters = vector(frame.root_meters, 3, 1e3, 'root_meters');
  const jointNames = Object.keys(map);
  exactly(frame.joints, jointNames, 'pose joints');
  const joints = Object.fromEntries(jointNames.map(key => [key, rotation(frame.joints[key], key)]));
  return {tick: frame.tick, source_revision, root_meters, joints};
}

/**
 * Frames are observational only. Exactly one advancing tick at a time;
 * duplicates must be byte-for-byte equivalent after validation. Rewind is
 * explicit via reset(). Mapping and VRM bone availability are checked early.
 */
export function createPoseProjector(vrm, config) {
  const map = validateBoneMap(config);
  if (!vrm?.humanoid || typeof vrm.humanoid.getNormalizedBoneNode !== 'function' ||
      typeof vrm?.scene?.position?.set !== 'function') {
    throw new TypeError('expected loaded VRM and mutable scene transform');
  }
  const bound = Object.entries(map).map(([source, target]) => {
    const bone = vrm.humanoid.getNormalizedBoneNode(target);
    if (!bone || typeof bone.rotation?.set !== 'function' || !bone.quaternion ||
        typeof bone.quaternion.set !== 'function') {
      throw new TypeError(`missing or incompatible normalized VRM bone: ${target}`);
    }
    return [source, bone];
  });
  let lastTick = null;
  let lastWire = null;
  function apply(frame) {
    const valid = validatePose(frame, map);
    const wire = JSON.stringify(valid);
    if (lastTick !== null) {
      if (valid.tick === lastTick && wire === lastWire) return 'duplicate';
      if (valid.tick !== lastTick + 1) throw new RangeError('pose tick discontinuity or conflicting duplicate');
    }
    // No animation mixer or physics update is driven by this adapter.
    for (const [key, bone] of bound) bone.quaternion.set(...valid.joints[key]);
    vrm.scene.position.set(...valid.root_meters);
    lastTick = valid.tick;
    lastWire = wire;
    return 'applied';
  }
  return Object.freeze({
    apply,
    reset: () => { lastTick = null; lastWire = null; },
    get lastTick() { return lastTick; },
  });
}
