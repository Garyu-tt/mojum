/** Read-only simulation pose -> three-vrm visual rotation projection.
 *
 * No import, animation library, actuators, network, or simulation mutation.
 * Pass an already loaded VRM and explicit joint-to-bone/axis bindings.
 * The VRMHumanoid.getNormalizedBoneNode API is documented by pixiv/three-vrm.
 */
const SCHEMA = 'mojum.vrm-measured-pose/1';
const SHA256 = /^[0-9a-f]{64}$/;
const JOINT_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
const BONES = new Set([
  'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
  'leftShoulder', 'rightShoulder',
  'leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'rightLowerArm',
  'leftHand', 'rightHand', 'leftUpperLeg', 'rightUpperLeg',
  'leftLowerLeg', 'rightLowerLeg', 'leftFoot', 'rightFoot',
  'leftToes', 'rightToes'
]);

export function createVrmPoseProjector(vrm, bindings) {
  if (!vrm?.humanoid || typeof vrm.humanoid.getNormalizedBoneNode !== 'function') {
    throw new TypeError('a VRM humanoid renderer is required');
  }
  if (!Array.isArray(bindings) || bindings.length < 1 || bindings.length > 128) {
    throw new TypeError('bindings must contain 1..128 joints');
  }
  const entries = [];
  const ids = new Set();
  const axes = new Set();
  for (const mapping of bindings) {
    if (mapping === null || typeof mapping !== 'object' ||
        Array.isArray(mapping) ||
        Object.keys(mapping).sort().join(',') !== 'axis,bone,id') {
      throw new TypeError('invalid binding fields');
    }
    const {id, bone, axis} = mapping;
    if (typeof id !== 'string' || !JOINT_ID.test(id) || ids.has(id) ||
        typeof bone !== 'string' || !BONES.has(bone) ||
        !['x', 'y', 'z'].includes(axis) || axes.has(bone + ':' + axis)) {
      throw new TypeError('invalid/duplicate joint or bone axis');
    }
    const node = vrm.humanoid.getNormalizedBoneNode(bone);
    if (!node?.rotation || !['x','y','z'].every(k => Number.isFinite(node.rotation[k]))) {
      throw new TypeError('normalized humanoid bone unavailable');
    }
    ids.add(id);
    axes.add(bone + ':' + axis);
    // Preserve the already-loaded avatar's rest pose for replay resets.
    entries.push({id, bone, axis, node, initialRotation: node.rotation[axis]});
  }

  let lastTick = -1;
  let lastSimulationTimeNs = -1;
  function apply(packet) {
    if (packet === null || typeof packet !== 'object' ||
        Array.isArray(packet) ||
        Object.keys(packet).sort().join(',') !==
          'joints,revision,schema,simulation_time_ns,tick' ||
        packet.schema !== SCHEMA ||
        !Number.isSafeInteger(packet.tick) || packet.tick < 0 ||
        packet.tick <= lastTick ||
        !Number.isSafeInteger(packet.simulation_time_ns) ||
        packet.simulation_time_ns <= lastSimulationTimeNs ||
        typeof packet.revision !== 'string' || !SHA256.test(packet.revision) ||
        !Array.isArray(packet.joints) || packet.joints.length !== entries.length) {
      throw new TypeError('invalid, stale or incomplete VRM pose snapshot');
    }
    const positions = new Map();
    for (const item of packet.joints) {
      if (item === null || typeof item !== 'object' || Array.isArray(item) ||
          Object.keys(item).sort().join(',') !== 'angle_rad,id' ||
          typeof item.id !== 'string' || !ids.has(item.id) ||
          positions.has(item.id) ||
          typeof item.angle_rad !== 'number' ||
          !Number.isFinite(item.angle_rad) ||
          Math.abs(item.angle_rad) > Math.PI) {
        throw new TypeError('invalid or duplicate measured virtual joint');
      }
      positions.set(item.id, item.angle_rad);
    }
    // All inputs are checked before applying any visual state.
    const prior = entries.map(e => e.node.rotation[e.axis]);
    try {
      entries.forEach(e => { e.node.rotation[e.axis] = positions.get(e.id); });
    } catch (error) {
      entries.forEach((e, i) => { e.node.rotation[e.axis] = prior[i]; });
      throw error;
    }
    lastTick = packet.tick;
    lastSimulationTimeNs = packet.simulation_time_ns;
    return {schema: SCHEMA, tick: lastTick, applied_joints: entries.length,
            visual_only: true};
  }

  function resetReplay() {
    // Resets a *visualization* stream, never the authoritative organism.
    // A normalized humanoid bone is not guaranteed to start at zero.
    // Reset the visualization to the captured pose, not an invented T-pose.
    entries.forEach(e => { e.node.rotation[e.axis] = e.initialRotation; });
    lastTick = -1;
    lastSimulationTimeNs = -1;
  }

  return Object.freeze({apply, resetReplay});
}
