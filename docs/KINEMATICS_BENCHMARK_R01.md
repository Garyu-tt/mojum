# Kinematics benchmark contract R01

Status: opt-in offline evaluator. It does **not** implement FK/IK, call Pinocchio, invoke MuJoCo/Jolt, or measure hardware itself.

## Purpose

Provide a stable CPU-first report contract for independently measured inverse-kinematics runs. A future optional Pinocchio adapter can run the solver in a separate process and submit bounded PUBLIC_SYNTHETIC observations here. The stdlib evaluator remains useful without Pinocchio installed.

## Wire input

Call `evaluate_ik_samples(samples, joint_limits, rig_revision=...)`.

- `provenance` must be exactly `PUBLIC_SYNTHETIC`.
- `joint_limits`: non-empty mapping of joint ID to `(lower_rad, upper_rad)`.
- Each sample has exactly `sample_id`, `target_m`, `achieved_m`, `joints`, `elapsed_ns`, `peak_bytes`.
- Targets and achieved positions are XYZ metres; joint positions and limits are radians; elapsed time is nanoseconds; peak memory is bytes.
- The same joint IDs must appear in every sample. IDs are unique and results are sorted by ID.
- Optional elapsed/memory thresholds produce an advisory budget flag; they do not imply real-time suitability.

## Report

The immutable report contains per-sample Euclidean position residual, limit compliance and maximum angular limit excursion; aggregate mean/max residual, mean/max elapsed time, maximum reported memory, and a SHA-256 consistency digest over canonical normalized content.

## Safety and interpretation

- Exact native numeric types are required; booleans, NaN, infinity, malformed IDs, duplicate IDs, oversized inputs and mismatched joint sets fail closed.
- Out-of-limit joint positions are reported, not silently clamped.
- The evaluator does not assert collision-free motion, physical contact, solver convergence, robot safety, or hardware suitability.
- Timing and memory are caller-supplied observations. The report does not independently verify them.
- No filesystem, network, wall clock, subprocess, mandatory third-party dependency, private Noosphere data, device access or runtime/Aquarium wiring.
- A solver adapter must separately pin and document Pinocchio version, build flags, license, CPU, rig/asset provenance, solver settings, repeat count and warm-up policy. Compare identical rigs, target sets, tolerances and fixed seeds; report cold-start separately from steady-state.
- Keep MuJoCo as the authoritative articulated physics candidate. This contract is an evaluation aid, not a physics-engine selection or replacement decision.

## Local verification

```sh
PYTHONPATH=src python -m unittest tests.test_kinematics_benchmark -v
```
