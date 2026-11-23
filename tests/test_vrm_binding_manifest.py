"""Hermetic contract tests for the optional Node.js VRM manifest validator."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools" / "vrm_binding_manifest.mjs"


@unittest.skipUnless(shutil.which("node"), "optional Node.js is not installed")
class VrmBindingManifestTests(unittest.TestCase):
    def run_node(self, source: str) -> None:
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", source, str(MODULE)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            completed.returncode, 0,
            msg=f"node failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_complete_mapping_and_stable_digest(self) -> None:
        self.run_node(r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const moduleUrl = pathToFileURL(process.argv[1]).href;
const {validateVrmBindingManifest} = await import(moduleUrl);
const base = {
  schema: 'mojum.vrm-binding-manifest/1',
  expected_joint_ids: ['hip.yaw', 'neck.pitch', 'left.elbow'],
  mappings: [
    {id:'hip.yaw', bone:'hips', axis:'y'},
    {id:'neck.pitch', bone:'neck', axis:'x'},
    {id:'left.elbow', bone:'leftLowerArm', axis:'z'}
  ],
  available_bones: ['hips', 'neck', 'leftLowerArm']
};
const copy = JSON.parse(JSON.stringify(base));
const first = validateVrmBindingManifest(base);
const second = validateVrmBindingManifest(copy);
assert.equal(first.valid, true);
assert.equal(first.missing_joint_ids.length, 0);
assert.equal(first.extra_joint_ids.length, 0);
assert.match(first.digest, /^[0-9a-f]{64}$/);
assert.equal(first.digest, second.digest);
assert.equal(base.mappings.length, 3);
assert.equal(Object.isFrozen(first), true);
assert.equal(Object.isFrozen(first.missing_joint_ids), true);
""")

    def test_missing_extra_duplicate_conflict_and_unavailable_bone(self) -> None:
        self.run_node(r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {validateVrmBindingManifest} = await import(pathToFileURL(process.argv[1]));
const report = validateVrmBindingManifest({
  schema:'mojum.vrm-binding-manifest/1',
  expected_joint_ids:['a','b','c'],
  mappings:[
    {id:'a',bone:'hips',axis:'x'},
    {id:'b',bone:'hips',axis:'x'},
    {id:'surplus',bone:'head',axis:'z'}
  ],
  available_bones:['hips']
});
assert.equal(report.valid, false);
assert.deepEqual(report.missing_joint_ids, ['c']);
assert.deepEqual(report.extra_joint_ids, ['surplus']);
assert.deepEqual(report.duplicate_bone_axes, ['hips:x']);
assert.deepEqual(report.unavailable_bones, ['head']);
assert.ok(report.duplicate_mapping_entries.includes('a'));
assert.ok(report.duplicate_mapping_entries.includes('b'));
""")

    def test_fail_closed_on_wrong_types_unknown_fields_and_bounds(self) -> None:
        self.run_node(r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {validateVrmBindingManifest} = await import(pathToFileURL(process.argv[1]));
const good = {
  schema:'mojum.vrm-binding-manifest/1',
  expected_joint_ids:['a'],
  mappings:[{id:'a',bone:'hips',axis:'x'}],
  available_bones:['hips']
};
assert.throws(() => validateVrmBindingManifest({...good, schema:'wrong'}), TypeError);
assert.throws(() => validateVrmBindingManifest({...good, extra:true}), TypeError);
assert.throws(() => validateVrmBindingManifest({...good, expected_joint_ids:'a'}), TypeError);
assert.throws(() => validateVrmBindingManifest({...good, mappings:[{id:'a',bone:'hips',axis:'q'}]}), TypeError);
const sparse = ['a']; sparse.length = 2;
assert.throws(() => validateVrmBindingManifest({...good, expected_joint_ids:sparse}), TypeError);
const accessor = {schema:good.schema, mappings:good.mappings,
  available_bones:good.available_bones};
Object.defineProperty(accessor, 'expected_joint_ids', {enumerable:true, get(){throw new Error('getter');}});
assert.throws(() => validateVrmBindingManifest(accessor), TypeError);
const withArrayExtra = ['a']; withArrayExtra.note = 'extra';
assert.throws(() => validateVrmBindingManifest({...good, expected_joint_ids:withArrayExtra}), TypeError);
""")

    def test_mapping_order_does_not_change_digest_or_mutate_inputs(self) -> None:
        self.run_node(r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {validateVrmBindingManifest} = await import(pathToFileURL(process.argv[1]));
const original = {
  schema:'mojum.vrm-binding-manifest/1',
  expected_joint_ids:['b','a'],
  mappings:[{id:'b',bone:'neck',axis:'x'},{id:'a',bone:'hips',axis:'y'}],
  available_bones:['neck','hips']
};
const reversed = {
  schema:original.schema,
  expected_joint_ids:['a','b'],
  mappings:[...original.mappings].reverse(),
  available_bones:['hips','neck']
};
const before = JSON.stringify(original);
const a = validateVrmBindingManifest(original);
const b = validateVrmBindingManifest(reversed);
assert.equal(a.valid, true);
assert.equal(a.digest, b.digest);
assert.equal(JSON.stringify(original), before);
""")

    def test_rejects_duplicate_expected_and_available_inventory(self) -> None:
        self.run_node(r"""
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {validateVrmBindingManifest} = await import(pathToFileURL(process.argv[1]));
const report = validateVrmBindingManifest({
  schema:'mojum.vrm-binding-manifest/1',
  expected_joint_ids:['a','a'],
  mappings:[{id:'a',bone:'hips',axis:'x'}],
  available_bones:['hips','hips']
});
assert.equal(report.valid, false);
assert.deepEqual(report.duplicate_expected_joint_ids, ['a']);
assert.deepEqual(report.duplicate_available_bones, ['hips']);
""")

if __name__ == "__main__":
    unittest.main()
