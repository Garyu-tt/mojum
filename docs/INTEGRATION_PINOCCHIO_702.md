# #702 / Pinocchio CPU FK/IK controlled spike (opt-in, not production physics)

This is **only the Pinocchio slice** of #702. Jolt/PBD cloth, MuJoCo contact, rig import, and canonical Aquarium integration remain unimplemented. Nothing in this module simulates hardware contact, handles live sensor feeds, uses network access, writes SQLite journals, or reads PRIVATE Noosphere events.

## Non-overlap with #722

This is an independent **native Jacobian-based damped least squares IK** experiment, including a real Pinocchio frame Jacobian and bounded iterative convergence. PR #722 instead benchmarks **analytic IK** checked with native Pinocchio FK. The two public 2R fixtures, report schemas, and module APIs are not interchangeable; do not merge both as duplicate canonical adapters without a deliberate #702 integration choice. This spike does not claim the entire #702 issue.

Direct Python API callers must supply an exact built-in string backend selector; equality-spoofing objects are rejected. Native IK rejects unrepresentably large integer targets as a documented ValueError before any foreign solver state is touched.

## Reproduction

No external dependencies for the public synthetic reference:

    PYTHONPATH=src python -m unittest discover -s tests -p 'test_pinocchio_benchmark.py' -v
    PYTHONPATH=src python -m mojum.pinocchio_benchmark --backend reference --samples 128

Optional **real** Pinocchio execution in a disposable environment:

    python3.11 -m venv .venv-pin-benchmark
    . .venv-pin-benchmark/bin/activate
    python -m pip install 'pin==4.1.0'
    PYTHONPATH=src python -m mojum.pinocchio_benchmark --backend pinocchio --samples 128

The upstream PyPI distribution is named **pin** and imports as **pinocchio**.
The benchmark requires the exact upstream module version 4.1.0. Missing
dependencies and version mismatches cause explicit failures; the analytic
reference is never misrepresented as a successful Pinocchio run.

## Contract and measured budgets

Schema: mojum.benchmark.pinocchio-2r/1. Public, synthetic two-joint Rz
chain: shoulder and elbow, link lengths 0.35/0.25 m; limits
[-2.8, +2.8] / [-2.5, +2.5] radians. Targets are fixed in source.
Both modes check FK against an independent trigonometric implementation.
Real Pinocchio mode additionally solves bounded damped-least-squares IK
using the native frame Jacobian and requires synthetic position error
no greater than one micrometer.

The physical_contact field is always false: no collision solver, cloth,
real-body input, or hardware authority is present. Samples are bounded
at 4096, IK iterations at 256 per case, with only two joints and four
fixed cases. These are input/iteration bounds, not a hard RAM ceiling.
The reported FK median nanoseconds are directly observed in-process
measurements, *not* deterministically reproducible CPU ratings.
Thermals, compiler build, clocks, native allocations and platform matter.
The stable replay tests intentionally ignore the timing field.
No RAM or power measurements are claimed.

## License, pin and limitations

- Upstream repository: https://github.com/stack-of-tasks/pinocchio
- Repository license: BSD-2-Clause.
- Distribution: https://pypi.org/project/pin/, pinned pin==4.1.0.
- PyPI metadata lists BSD-3-Clause. Check the exact wheel contents,
  transitive native binary licenses and platform support before shipping.
- No upstream assets, binaries or model data are vendored.

Real articulated model import, Pinocchio vs MuJoCo contact measurements,
Jolt/PBD cloth, CPU/RAM profiling across architectures, and actual
Avatar/GENESIS DOF contract validation remain future acceptance work.
Nothing here should be used to command real hardware or silently change
the released point-room.
