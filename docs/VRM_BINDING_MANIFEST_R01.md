# VRM binding manifest R0.1

## Purpose

`mojum.vrm-binding-manifest/1` is a deterministic, offline admission check for a **complete mapping explicitly requested by the caller**. It sits before the existing read-only `vrm_pose_projector.mjs`; it does not replace that projector and does not load a VRM asset.

The caller supplies:
- `expected_joint_ids`: the exact joint inventory required for this visualization profile;
- `mappings`: one `{id, bone, axis}` record per requested joint;
- `available_bones`: the humanoid bones actually reported by the already-loaded avatar.

The validator never assumes that an avatar must contain all 53 rig joints. Completeness is measured only against the explicit caller-provided inventory.

## Validation

The validator enforces native plain records and dense arrays, exact field sets, bounded cardinalities, stable identifiers, known VRM humanoid bone names, and x/y/z axes. It reports missing and extra joint IDs, duplicate IDs, duplicate bone-axis targets, duplicate available bones, and mappings whose target bone is not available.

Malformed types and unsupported schema/fields throw `TypeError`. A structurally valid but incomplete or conflicting mapping returns `valid: false` with sorted diagnostics.

## Digest and determinism

The SHA-256 digest covers canonical normalized input metadata. Joint inventories, mappings and available bones are sorted before serialization, so permutations of equivalent input lists produce the same digest. The digest is a consistency fingerprint, **not authentication**.

Reflection failures from hostile JavaScript proxies are normalized to `TypeError`, preserving the validator fail-closed input contract.

## Run

With Node.js available:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_vrm_binding_manifest.py' -v
```

Without Node.js, the test module skips the optional runtime tests. The core Mojum Python runtime remains independent of Node and three-vrm.

## Boundaries

This is metadata validation only. It does not prove anatomical calibration, coordinate-frame correctness, angle conventions, retargeting quality, renderer behavior, timing/replay correctness, or physical safety. It performs no asset loading, network access, file I/O, simulation mutation, persistence, Noosphere access, or motor control.
