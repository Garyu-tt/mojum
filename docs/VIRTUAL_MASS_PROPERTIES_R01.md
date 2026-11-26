# Virtual rigid-body mass properties R0.1

## Purpose

`mojum.virtual_mass_properties` is an opt-in, offline, CPU/stdlib-only
calculator for aggregate mass properties of a small **PUBLIC_SYNTHETIC**
rigid-body fixture. It calculates total mass, world-space centre of mass,
and the inertia tensor about that aggregate centre of mass.

This is not a physics engine, a calibrated anatomical model, a stability
certificate, or evidence of physical contact. It does not load assets,
connect to MuJoCo, control motors, read a journal, or alter Aquarium.

## Input contract

Call `build_mass_properties(raw)` with a native JSON-shaped dictionary:

- `schema`: exactly `mojum.virtual-mass-properties/1`
- `visibility`: exactly `PUBLIC_SYNTHETIC`
- `segments`: 1–64 segment records

Each segment has exactly:

- `id`: unique printable ASCII identifier, 1–64 characters
- `mass_kg`: positive finite mass in kilograms, at most 10,000 kg
- `center_m`: local centre of mass `[x, y, z]`, in metres
- `inertia_kg_m2`: symmetric positive-semidefinite 3×3 inertia tensor
  about the segment's own centre of mass, in kg·m²
- `rotation`: proper orthonormal 3×3 local-to-world rotation matrix
  (determinant +1)
- `translation_m`: local-frame origin in world coordinates, in metres

All numeric fields must be native Python `int` or `float`, not booleans
or subclasses, and finite. Position components are bounded to 10,000 m;
inertia entries are bounded to 100,000,000 kg·m²; aggregate mass is bounded
to 100,000 kg. Unknown/missing fields, duplicate IDs, reflections,
non-orthonormal matrices, asymmetric, indefinite, or non-realizable inertia (principal-moment
triangle inequality), unsupported
visibility and exhausted budgets fail closed with `MassPropertiesError`.

## Calculation

For each segment, transform its local COM into world coordinates:

`cᵢ = Rᵢ cᵢ(local) + tᵢ`

Rotate its centroidal inertia tensor:

`Iᵢ(world) = Rᵢ Iᵢ(local) Rᵢᵀ`

Compute total mass and aggregate COM:

`M = Σ mᵢ`

`C = (Σ mᵢ cᵢ) / M`

Then shift each tensor to the aggregate COM using the parallel-axis theorem:

`I(C) = Σ [Iᵢ(world) + mᵢ ((dᵢ·dᵢ) 1 - dᵢ dᵢᵀ)]`

where `dᵢ = cᵢ - C`.

Summation uses `math.fsum`; input segment order is normalized by identifier.
The output is a frozen dataclass containing detached tuples, an input SHA-256
and a result SHA-256. These digests provide deterministic integrity evidence,
not authentication or proof that the supplied measurements are true.

## Example

```python
from mojum.virtual_mass_properties import build_mass_properties

identity = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
raw = {
    "schema": "mojum.virtual-mass-properties/1",
    "visibility": "PUBLIC_SYNTHETIC",
    "segments": [
        {
            "id": "left",
            "mass_kg": 2.0,
            "center_m": [0.0, 0.0, 0.0],
            "inertia_kg_m2": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
            "rotation": identity,
            "translation_m": [-1.0, 0.0, 0.0],
        },
        {
            "id": "right",
            "mass_kg": 2.0,
            "center_m": [0.0, 0.0, 0.0],
            "inertia_kg_m2": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
            "rotation": identity,
            "translation_m": [1.0, 0.0, 0.0],
        },
    ],
}
properties = build_mass_properties(raw)
print(properties.total_mass_kg)        # 4.0
print(properties.center_of_mass_m)     # (0.0, 0.0, 0.0)
print(properties.inertia_about_com_kg_m2)
# ((0.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0))
```

## Resource and failure boundaries

The implementation accepts at most 64 segments and performs a fixed amount
of 3×3 matrix arithmetic per segment. It imports only the Python standard
library and has no file, socket, subprocess, SQLite, model, or native-engine
access. Malformed input and numeric overflow are rejected before a result is
returned. Each segment must have strictly positive mass; zero-mass helper
nodes are not part of this contract.

## Verification

Run the focused regression suite from the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_virtual_mass_properties.py' -v
```

The fixture suite covers the parallel-axis theorem, mass-weighted COM,
rotated inertia, order-independent results, immutability, invalid tensors,
hostile types, malformed transforms, privacy classification and resource
budgets. Exact-head GitHub CI and independent review are required before
integration. No new mandatory dependency or schema migration is introduced.
