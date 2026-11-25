# Kinematics sample evaluator R01

Status: opt-in offline evaluator. It does **not** implement FK/IK, call Pinocchio, invoke MuJoCo/Jolt, or measure hardware itself.

## Purpose

Provide a stable CPU-first report contract for independently measured inverse-kinematics runs. A future optional Pinocchio adapter can run the solver in a separate process and submit bounded PUBLIC_SYNTHETIC observations here. The stdlib evaluator remains useful without Pinocchio installed.

The module is `mojum.kinematics_evaluator` and its report schema is
`mojum.kinematics-evaluation/1`. It computes results from raw samples; the separate
`mojum.kinematics-benchmark/1` evidence-container contract is not interchangeable
with this evaluator's input or output.

## Wire input

Call `mojum.kinematics_evaluator.evaluate_ik_samples(samples, joint_limits, rig_revision=...)`.

- `provenance` must be exactly `PUBLIC_SYNTHETIC`.
- `joint_limits`: non-empty mapping of joint ID to `(lower_rad, upper_rad)`.
- Each sample has exactly `sample_id`, `target_m`, `achieved_m`, `joints`, `elapsed_ns`, `peak_bytes`.
- Targets and achieved positions are XYZ metres; joint positions and limits are radians; elapsed time is nanoseconds; peak memory is bytes.
- The same joint IDs must appear in every sample. IDs are unique and results are sorted by ID.
- Optional elapsed/memory thresholds produce an advisory budget flag; they do not imply real-time suitability.

## Numeric domain

Coordinates, joint bounds and joint positions accept finite built-in `float`
values and built-in `int` values only when conversion to `float` preserves the
integer exactly. For example, `2**53 + 1` and its negative are rejected, while
`2**53 + 2` and larger exactly representable integers remain admissible. This
prevents rounding from turning distinct coordinates into a zero residual or an
out-of-limit integer into a compliant position. Arithmetic remains floating-point,
not arbitrary-precision geometry.

Native values are compared before calculating an out-of-limit excursion. Values
inside a valid range, including the endpoints of `[-1e308, 1e308]`, have zero
excursion. Derived Cartesian residuals and nonnegative joint excursions must be
finite; overflow is rejected with `ValueError` before report construction or JSON
serialization. Large finite residuals remain supported, including the mean of
multiple `1e308` samples. Elapsed-time and memory counters retain their separate
bounded-integer admission rules.

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
PYTHONPATH=src python -m unittest discover -s tests -p 'test_kinematics_evaluator.py' -v
```
