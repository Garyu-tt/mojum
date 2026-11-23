# Virtual forward kinematics R0.1

## Purpose

`mojum.virtual_kinematics` evaluates a bounded synthetic articulated rig into
world-space joint-origin positions and 3×3 rotation matrices. It is a small
CPU-first geometry primitive for later body/pose experiments.

It does **not** integrate MuJoCo, resolve collisions, estimate anatomy, infer
motion from sensors, control an actuator, or authenticate who produced a rig.
A valid digest proves only internal consistency of the returned report.

## Input

The only accepted schema is `mojum.virtual-rig/1` with
`visibility="PUBLIC_SYNTHETIC"`. The input contains a root ID and an ordered
list of joints. Each joint has:

- `id`: unique bounded identifier.
- `parent`: null for the single root, otherwise an identifier already seen.
- `origin_m`: local translation from the parent's joint origin.
- `axis`, `angle_rad`: local revolute axis and angle.
- `min_rad`, `max_rad`: declared inclusive limits.

The axis is normalized by the evaluator and must be non-zero. Each joint
rotation is applied after its local translation. The parent transform rotates
the child's local translation before it is added to the parent's world
position. Inputs are bounded to 128 joints and finite numeric ranges.

## Output

The evaluator returns `mojum.virtual-kinematics-result/1` with world-space
positions, row-major local-to-world rotation matrices, source schema, joint
count and a SHA-256 self-consistency digest. It explicitly sets
`synthetic=true`, `physical_contact=false`, and
`physical_actuation=false`. `verify_kinematics_report` recomputes the digest;
it does not authenticate the caller or provide tamper-proof storage. The verifier accepts only an acyclic tree of exact built-in JSON types, bounded to 8,192 values, depth 16 and 256 KiB of UTF-8 text; malformed or over-budget reports return `false` before canonical serialization.

## Example

```python
from math import pi
from mojum.virtual_kinematics import evaluate_forward_kinematics

rig = {
    "schema": "mojum.virtual-rig/1",
    "visibility": "PUBLIC_SYNTHETIC",
    "root": "pelvis",
    "joints": [
        {
            "id": "pelvis", "parent": None,
            "origin_m": [0, 0, 0], "axis": [0, 0, 1],
            "angle_rad": 0, "min_rad": -pi, "max_rad": pi,
        },
        {
            "id": "spine", "parent": "pelvis",
            "origin_m": [0, 0, 0.4], "axis": [0, 1, 0],
            "angle_rad": 0.2, "min_rad": -0.8, "max_rad": 0.8,
        },
    ],
}
report = evaluate_forward_kinematics(rig)
```

## Verification

From the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_virtual_kinematics.py' -v
```

No mandatory dependencies outside the Python standard library are required.
The new module is opt-in and does not modify existing rig, joint, physics,
SQLite journal, or motor schemas.
