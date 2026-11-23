# Rig Joint Map R0.1

## Purpose

Map exact source-rig joint names to stable Mojum semantic joint IDs without fuzzy matching or implicit assumptions. This is an offline metadata contract for a future importer or MuJoCo adapter.

## API

The source descriptor contains rig_id, revision, and a list named joints.
The target descriptor contains rig_id, revision, and a list named joint_ids.
The bindings dictionary maps every target ID to one exact source joint name.

The optional aliases dictionary maps an alternate source spelling to an exact member of the source inventory. Alias chaining, fuzzy matching, and alias shadowing are rejected. Every target ID must have exactly one distinct source joint. Unused source joints are allowed because source rigs may include helper or facial joints.

## Bounds and failure behavior

- Plain built-in dictionaries, lists and strings only; subclasses are rejected.
- At most 256 source joints, target IDs and aliases.
- Stable IDs are ASCII identifiers using letters, digits, underscore, dot, colon or hyphen.
- Names and revisions are exact, case-sensitive UTF-8 strings, up to 96 characters; all Unicode Cc control characters (including C1) are rejected.
- Missing/unknown target IDs, unknown source names, duplicate inventories, duplicate source bindings, malformed descriptors and invalid aliases raise JointMapError.
- Returned JointMap and JointBinding values are frozen dataclasses with tuple bindings. as_dict() returns a detached JSON-safe copy.
- SHA-256 identifies canonical receipt bytes. It is not a signature, authentication, source-asset provenance, or physical-rig attestation.

## Explicit non-goals

No GLB parsing, asset loading, geometry, joint transforms, FK/IK, calibration, MuJoCo/Jolt runtime, sensor data, motor commands, actuation, persistence, network access, PRIVATE records or edits to canonical runtime.
