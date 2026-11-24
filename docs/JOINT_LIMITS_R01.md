# Joint-limit envelope auditor R01

Schema: mojum.joint-limits/1

A bounded, offline, standard-library validator for caller-supplied scalar joint limits and ordered pose samples.

## API

Call audit_joint_limits(limits, samples, rig_revision="rig-a"). Limits are (joint_id, lower, upper) records. Samples are (logical_tick, rig_revision, positions_dict) records.

Position limits are inclusive. Every sample must contain exactly the declared joints. Ticks are consecutive; all samples must use the requested rig revision. Output is a frozen report with sorted joint IDs, observed extrema and a SHA-256 consistency digest.

## Bounds and rejection

At most 256 joints, 4096 samples, 64-character ASCII joint IDs and 128-character rig revisions. Booleans, non-native numeric types, NaN/Infinity, duplicate IDs, missing/extra joints, inverted limits, out-of-range positions, tick gaps and revision changes are rejected.

## Safety boundary

This is only a metadata/trajectory audit. It does not infer revolute wrap semantics, perform kinematics or physics, establish anatomical calibration, command actuators, or serve as a safety interlock. Caller-supplied PUBLIC_SYNTHETIC labeling is not provenance authentication. No filesystem, network, third-party dependency, Noosphere, device, Aquarium or Graph IR access occurs.

## Test

PYTHONPATH=src python -m unittest tests.test_joint_limits -v

Run independent exact-head CI and review before merge. This branch is a draft implementation, not an accepted production safety component.
