# Planar kinematics reference R0.1 (#702)

A dependency-free mathematical fixture for checking future FK/IK adapters
(including optional Pinocchio experiments) against known answers. It is a
**planar two-link chain**, not a humanoid skeleton, 3D solver, contact model,
or motor controller.

## Contract
- Link lengths and Cartesian positions are metres; angles and limits are radians.
- Link lengths must be at least `1e-150` metres and at most `1e100` metres, preventing denominator underflow in the analytic IK formula.
- FK uses shoulder angle followed by relative elbow angle.
- IK exposes elbow-up and elbow-down branches.
- Unreachable targets and solutions outside inclusive joint limits return no
  solution. Malformed/non-finite inputs raise ValueError.
- Results report achieved endpoint and Euclidean residual in metres.
- No hidden angle wrapping; explicit limits remain meaningful.

## Run
```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_planar_kinematics_reference.py' -v
python tools/planar_kinematics_reference.py
```

The CLI emits compact JSON using schema
`mojum.planar-kinematics-reference/1`. No randomness, wall-clock fields,
network access, native dependencies, or Aquarium/Noosphere writes.

## Benchmark use and limits
Keep model/rig revision, units, target poses, joint limits, tolerances, and
CPU/RAM measurement procedure identical across future engines. This fixture
establishes mathematical conventions and simple reachable/unreachable/limit
vectors only. It does not prove Pinocchio/Jolt availability, performance,
3D humanoid correctness, dynamics, collisions, or cloth behavior. Do not
compare its analytic runtime to iterative native solver timings as equivalent
workloads.
