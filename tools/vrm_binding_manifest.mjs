/** Strict, deterministic admission for a complete caller-declared VRM binding set.
 *
 * This validates metadata only. It does not load an avatar, infer anatomy,
 * mutate a renderer, or establish calibrated coordinate-frame semantics.
 */
import {createHash} from 'node:crypto';

export const VRM_BINDING_SCHEMA = 'mojum.vrm-binding-manifest/1';
const MAX_JOINTS = 128;
const MAX_BONES = 22;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;
const AXES = new Set(['x', 'y', 'z']);
const BONES = new Set([
  'hips', 'spine', 'chest', 'upperChest', 'neck', 'head',
  'leftShoulder', 'rightShoulder',
  'leftUpperArm', 'rightUpperArm', 'leftLowerArm', 'rightLowerArm',
  'leftHand', 'rightHand', 'leftUpperLeg', 'rightUpperLeg',
  'leftLowerLeg', 'rightLowerLeg', 'leftFoot', 'rightFoot',
  'leftToes', 'rightToes'
]);

function fail(message) {
  throw new TypeError(message);
}

function plainRecord(value, requiredKeys, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    fail(label + ' must be a plain record');
  }
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) fail(label + ' must be a plain record');
  const keys = Reflect.ownKeys(value);
  if (keys.some(k => typeof k !== 'string') ||
      keys.slice().sort().join('\0') !== requiredKeys.slice().sort().join('\0')) {
    fail(label + ' has missing or unknown fields');
  }
  const result = Object.create(null);
  for (const key of requiredKeys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !Object.prototype.hasOwnProperty.call(descriptor, 'value') ||
        descriptor.enumerable !== true) {
      fail(label + ' fields must be enumerable data properties');
    }
    result[key] = descriptor.value;
  }
  return result;
}

function denseArray(value, min, max, label) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype ||
      value.length < min || value.length > max) {
    fail(label + ' must be a bounded native array');
  }
  const keys = Reflect.ownKeys(value);
  if (keys.length !== value.length + 1 || keys.some(k =>
    k !== 'length' && (typeof k !== 'string' || !/^(0|[1-9][0-9]*)$/.test(k) ||
      Number(k) >= value.length))) {
    fail(label + ' must be dense and have no extra properties');
  }
  const out = [];
  for (let i = 0; i < value.length; i++) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(i));
    if (!descriptor || !Object.prototype.hasOwnProperty.call(descriptor, 'value') ||
        descriptor.enumerable !== true) fail(label + ' must contain data elements');
    out.push(descriptor.value);
  }
  return out;
}

function stringList(value, min, max, label, allowed = null) {
  const items = denseArray(value, min, max, label);
  for (const item of items) {
    if (typeof item !== 'string' || !IDENTIFIER.test(item) ||
        (allowed && !allowed.has(item))) fail(label + ' contains an invalid entry');
  }
  return items;
}

function freezeResult(value) {
  for (const item of Object.values(value)) {
    if (Array.isArray(item)) Object.freeze(item);
  }
  return Object.freeze(value);
}

/**
 * Input:
 * {schema, expected_joint_ids, mappings:[{id,bone,axis}], available_bones}
 *
 * Structural/type violations throw TypeError. A well-formed but incomplete or
 * conflicting mapping returns valid:false with deterministic diagnostic lists.
 */
export function validateVrmBindingManifest(input) {
  const record = plainRecord(input,
    ['schema', 'expected_joint_ids', 'mappings', 'available_bones'], 'manifest');
  if (record.schema !== VRM_BINDING_SCHEMA) fail('unsupported manifest schema');

  const expected = stringList(record.expected_joint_ids, 1, MAX_JOINTS,
    'expected_joint_ids');
  const available = stringList(record.available_bones, 0, MAX_BONES,
    'available_bones', BONES);
  const rawMappings = denseArray(record.mappings, 0, MAX_JOINTS, 'mappings');

  const duplicateJointIds = new Set();
  const seenExpected = new Set();
  for (const id of expected) {
    if (seenExpected.has(id)) duplicateJointIds.add(id);
    seenExpected.add(id);
  }
  const duplicateAvailable = new Set();
  const availableSet = new Set();
  for (const bone of available) {
    if (availableSet.has(bone)) duplicateAvailable.add(bone);
    availableSet.add(bone);
  }

  const mappings = [];
  const seenIds = new Set();
  const duplicateMappings = new Set();
  const targetOwners = new Map();
  const duplicateTargets = new Set();
  for (const raw of rawMappings) {
    const mapping = plainRecord(raw, ['id', 'bone', 'axis'], 'mapping');
    const {id, bone, axis} = mapping;
    if (typeof id !== 'string' || !IDENTIFIER.test(id) ||
        typeof bone !== 'string' || !BONES.has(bone) ||
        typeof axis !== 'string' || !AXES.has(axis)) {
      fail('mapping contains an invalid joint, bone or axis');
    }
    if (seenIds.has(id)) duplicateMappings.add(id);
    seenIds.add(id);
    const target = bone + ':' + axis;
    if (targetOwners.has(target)) {
      duplicateTargets.add(target);
      duplicateMappings.add(targetOwners.get(target));
      duplicateMappings.add(id);
    } else {
      targetOwners.set(target, id);
    }
    mappings.push({id, bone, axis});
  }

  const expectedSet = new Set(expected);
  const extraJointIds = [...seenIds].filter(id => !expectedSet.has(id)).sort();
  const missingJointIds = [...expectedSet].filter(id => !seenIds.has(id)).sort();
  const unavailableBones = [...new Set(mappings
    .filter(m => !availableSet.has(m.bone)).map(m => m.bone))].sort();
  const duplicateExpected = [...duplicateJointIds].sort();
  const duplicateBoneInventory = [...duplicateAvailable].sort();
  const duplicateMappingEntries = [...duplicateMappings].sort();
  const duplicateBoneAxes = [...duplicateTargets].sort();

  const canonical = {
    schema: VRM_BINDING_SCHEMA,
    expected_joint_ids: [...expected].sort(),
    mappings: mappings.slice().sort((a, b) =>
      a.id.localeCompare(b.id) || a.bone.localeCompare(b.bone) ||
      a.axis.localeCompare(b.axis)),
    available_bones: [...available].sort()
  };
  const digest = createHash('sha256')
    .update(JSON.stringify(canonical), 'utf8').digest('hex');

  const valid = missingJointIds.length === 0 && extraJointIds.length === 0 &&
    duplicateExpected.length === 0 && duplicateBoneInventory.length === 0 &&
    duplicateMappingEntries.length === 0 && duplicateBoneAxes.length === 0 &&
    unavailableBones.length === 0 && expected.length === mappings.length;

  return freezeResult({
    schema: VRM_BINDING_SCHEMA,
    valid,
    expected_joint_count: expected.length,
    mapping_count: mappings.length,
    missing_joint_ids: missingJointIds,
    extra_joint_ids: extraJointIds,
    duplicate_expected_joint_ids: duplicateExpected,
    duplicate_mapping_entries: duplicateMappingEntries,
    duplicate_bone_axes: duplicateBoneAxes,
    duplicate_available_bones: duplicateBoneInventory,
    unavailable_bones: unavailableBones,
    digest
  });
}
