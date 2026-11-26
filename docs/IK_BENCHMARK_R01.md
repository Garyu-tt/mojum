# Synthetic planar IK benchmark R0.1

## Purpose

This is a dependency-free CPU fixture for comparing two numerical baselines:
a closed-form two-link inverse-kinematics solver and cyclic coordinate
descent (CCD) for 1–16 planar links. It establishes a repeatable contract for
later comparison with a pinned Pinocchio build. It does **not** implement
Pinocchio, Jolt, rigid-body dynamics, cloth, collision/contact, calibration,
or physical hardware sensing.

## Run

From the repository root:

```sh
PYTHONPATH=src python -m unittest tests.test_ik_benchmark -v
python tools/ik_benchmark.py
```

The module uses only the Python 3.11+ standard library. It installs nothing,
opens no files, makes no network calls, and has no runtime/Aquarium wiring.

## Contract

Schema: `mojum.ik-benchmark/1`. Inputs are explicit synthetic link lengths,
a planar target, absolute per-joint angle limits in radians, and optionally
an initial pose. Inputs are bounded to 16 joints, 256 benchmark cases, 4,096
CCD iterations, finite coordinates with absolute magnitude <= 1,000,000,
and one-revolution-or-smaller limit spans. Boolean values are rejected where
numeric values are expected.

The analytic baseline accepts exactly two links and fails closed for an
unreachable target or a solution outside supplied limits. CCD clamps every
joint update to its declared absolute limit. A result includes the endpoint,
target, residual in metres, iteration/evaluation counts, convergence flag,
and a SHA-256 digest over canonical result/input values. The digest excludes
wall-clock and memory observations.

The benchmark runner records elapsed nanoseconds and Python-traced peak
allocation bytes as **observations**, not deterministic performance claims.
Compare results only on the same host/interpreter and report Python version,
CPU model, operating mode, repetitions and warm-up policy externally. No
cross-platform performance ranking is implied by a single run.

## Next integration study

For Pinocchio, freeze an identical synthetic planar-equivalent chain/target
set, document the URDF/model construction and exact package commit/version,
then compare FK endpoint, IK residual, joint-limit compliance, iteration
budget, CPU time and peak resident memory. Pinocchio's native solver and
license/build requirements must be evaluated separately; it is not imported
by this fixture.

Jolt/PBD cloth is a separate workload. It requires a distinct reproducible
cloth mesh, solver configuration, native binding/license review and CPU/RAM
budget. It must not be conflated with this point-chain IK benchmark.

No simulated contact in this benchmark is evidence of physical contact.
No result may authorize actuators or alter the stable Aquarium physics choice.
