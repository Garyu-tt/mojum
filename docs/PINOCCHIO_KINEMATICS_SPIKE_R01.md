# #702: optional Pinocchio 2-DOF kinematics spike (R0.1)

Status: **experimental, opt-in, NOT integrated into Aquarium, body controller or Noosphere**. This benchmark is exclusively virtual; positions are synthetic metres/radians, **not hardware contact**, torque, collision, a cloth solver, or rig approval. It does not install dependencies or load PRIVATE histories, external models or arbitrary URDF files.

## Reproducible CPU fixture

```bash
PYTHONPATH=src python -m unittest tests.test_optional_pinocchio_benchmark -v
PYTHONPATH=src python -m mojum.optional_pinocchio_benchmark --backend analytic --seed 702 --cases 32
```

Two locally generated revolute-Z joints, link lengths 0.40/0.30 m, joint limits +/-2.60/2.50 radians, fixed tool tip. The independent closed-form analytic 2-link IK is evaluated on seeded reachable targets. Each IK result is verified by the selected FK engine. No motion planning, contact, dynamics or actuator output occurs. The result schema is `mojum.kinematics.pinocchio-benchmark/1`, with FK and IK residual errors in **metres**, case runtime in **nanoseconds** and explicit `virtual_only=true`. Timing numbers are **observations**, not portable benchmarks; repeat across platforms and record CPU/memory externally before a backend decision.

Optional external engine, in a fresh isolated Python 3.11+ environment only, after dependency and binary license review:

```bash
python -m venv /tmp/mojum-pin-702
/tmp/mojum-pin-702/bin/python -m pip install 'pin==4.1.0'
PYTHONPATH=src /tmp/mojum-pin-702/bin/python -m mojum.optional_pinocchio_benchmark --backend pinocchio --seed 702 --cases 32
```

`pin` is the distribution, `pinocchio` the Python import. Pinocchio loads only the static locally generated in-memory URDF; a model with anything other than 2 configuration/velocity DOFs or an absent tool frame aborts. The optional path rejects versions other than `pin==4.1.0` and has not been exercised in the stdlib CI. Pinocchio FK must match independent analytic FK to 1e-8 m, and the independently solved IK must match Pinocchio FK to the same tolerance. The **IK solver is analytic**, not a Pinocchio iterative solver. The Python package install and its transitive wheels are **not yet accepted for distribution**.

## Failure/acceptance boundaries

- Strict finite real coordinates; booleans, NaNs, infinite values, invalid 2-DOF limits, out-of-range commands and unreachable IK targets fail closed.
- Case count capped at 128, seed is unsigned 32-bit, no files read/written, no network, no JSON journal mutations.
- Test fixture does not claim support for arbitrary GENESIS rig nodes, joint semantics, global collision, body contact, force, torque, or motor actuation.
- Before promotion: test real Pinocchio in isolated OS/Python matrix, pin all transitive wheels and their licenses; measure FK/IK CPU wall time and peak RSS; test representative generated GENESIS DOF/rig and joint-frame axis mapping; compare against MuJoCo pinned fixtures; independently review exact PR HEAD/CI.
- JoltPhysics: upstream **MIT**, rigid-body/collision C++ library; optional language bindings exist, but no upstream cloth solver or Python binding/ABI has been demonstrated for this use case. **Cloth comparison is NOT implemented**, and PBD cloth must not be attributed to Jolt without independent evidence. Jolt is a separate spike; do not insert a dependency on it here.

## Upstream licensing checklist (not clearance)

- Pinocchio source: `stack-of-tasks/pinocchio`, `BSD-2-Clause` per upstream repository/ROS index; Python `pin==4.1.0` PyPI metadata reports `BSD-3-Clause` for the distribution. Reconcile source/wheel/transitive SPDX notices before redistribution. This isolated local spike makes no redistributability claim.
- Pinocchio API: `buildModelFromXML`, `forwardKinematics`, `updateFramePlacements`, frame `oMf` translation.
- JoltPhysics source: `jrouwe/JoltPhysics`, MIT. Neither Pinocchio nor Jolt are Mojum baseline dependencies.

Source references: https://github.com/stack-of-tasks/pinocchio ; https://pypi.org/project/pin/ ; https://gepettoweb.laas.fr/doc/stack-of-tasks/pinocchio/jnrh2023/template/parsers.html ; https://github.com/jrouwe/JoltPhysics
