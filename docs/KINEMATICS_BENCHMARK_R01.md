# Kinematics benchmark evidence contract R0.1

## Purpose and boundary

`mojum.kinematics-benchmark/1` is a dependency-free admission format for results emitted by a separately controlled synthetic FK/IK benchmark. It is not a kinematics solver, physics engine, hardware interface, calibration source, or proof that the submitted measurements are truthful.

Inputs must be explicitly synthetic and public. The contract never reads assets, files, network services, SQLite journals, or Noosphere records. It does not modify the canonical rig, Graph IR, Aquarium, or motor policy. No runtime integration is performed.

## Measurements and units

- FK: sample count and maximum positional error in micrometres.
- IK: attempts, converged attempts, maximum residual in micrometres, and joint-limit violation count.
- Contacts: count only, explicitly labelled `SIMULATED_ONLY`. This is not a physical contact sensor.
- Resources: CPU p50/p95 in microseconds and maximum resident set size in KiB.
- Provenance: rig ID/revision, DOF count, solver name/version/license, case ID and deterministic seed.

The producer must pin upstream revisions and record platform/compiler/thread settings alongside the report when publishing a benchmark study. This first contract intentionally does not assert those external facts.

## Validation and determinism

The stdlib-only API rejects non-native numeric types (including bool), malformed strings, non-finite values, inverted CPU percentiles, inconsistent IK counts, unsupported contact authority, unknown report fields, and values outside documented bounds. Canonical sorted-key JSON is sealed with SHA-256. Verification revalidates semantics after checking the digest.

A digest detects accidental or post-generation modification; it does not authenticate the producer or prove benchmark integrity. Reports contain no raw trajectories, asset bytes, private identifiers, or arbitrary metadata.

## Resource and dependency policy

No third-party dependency is added. Report construction is linear in a fixed, small number of scalar fields and uses bounded strings. The existing package remains Python 3.11+ and CPU-first. The module is opt-in and has no import-time work beyond stdlib definitions.

## Verification

Run:

```sh
python -m unittest tests.test_kinematics_benchmark -v
```

The test suite covers deterministic receipts, mutation/tamper detection, exact native types, bounds, inconsistent counters, percentile ordering, and simulated-only contact semantics.

## Explicitly not delivered

No Pinocchio/Jolt binding, solver comparison, numerical benchmark run, x86/ARM measurement, cloth simulation, body integration, or production readiness claim is included. Those require separately pinned upstream builds and controlled benchmark hosts.
