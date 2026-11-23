# Cloth material profile R0.1

## Purpose

This opt-in, solver-neutral contract validates caller-supplied synthetic cloth constitutive parameters before an adapter maps them to a solver. It is not a cloth solver, material database, calibration tool, or claim that a coefficient set matches a real fabric.

## Version and classification

- Schema: `mojum.cloth.material-profile/1`
- Required classification: `PUBLIC_SYNTHETIC`
- Input: one exact built-in Python `dict` with exactly the fields below.
- Runtime: Python 3.11+, standard library only.
- Output: frozen `ClothMaterialProfile`, detached mapping export, and unkeyed SHA-256 content fingerprint.

## Fixed-point SI fields

| Field | Stored unit | Scale to SI | Accepted stored range |
|---|---|---:|---:|
| `areal_density_ug_m2` | micro-kg/m² | 10⁻⁶ kg/m² | 1 .. 1,000,000,000,000 |
| `thickness_nm` | nm | 10⁻⁹ m | 1 .. 1,000,000,000 |
| `stretch_stiffness_uN_m` | micro-N/m | 10⁻⁶ N/m | 1 .. 1,000,000,000,000,000 |
| `bend_stiffness_uNm` | micro-N·m | 10⁻⁶ N·m | 0 .. 1,000,000,000,000 |
| `damping_ratio_ppm` | parts per million | 10⁻⁶ | 0 .. 1,000,000 |
| `static_friction_ppm` | parts per million | 10⁻⁶ | 0 .. 10,000,000 |
| `dynamic_friction_ppm` | parts per million | 10⁻⁶ | 0 .. 10,000,000 |

These broad admission bounds prevent nonsensical or unbounded inputs; they are not recommended operating ranges. Dynamic friction must not exceed static friction. Zero bend stiffness and zero damping are allowed as explicit synthetic parameters.

## Example

```python
from mojum.cloth_material import validate_material_profile

profile = validate_material_profile({
    "schema": "mojum.cloth.material-profile/1",
    "classification": "PUBLIC_SYNTHETIC",
    "areal_density_ug_m2": 180_000,
    "thickness_nm": 250_000,
    "stretch_stiffness_uN_m": 12_000_000,
    "bend_stiffness_uNm": 75,
    "damping_ratio_ppm": 20_000,
    "static_friction_ppm": 450_000,
    "dynamic_friction_ppm": 300_000,
})
print(profile.fingerprint)
```

## Failure behavior and trust limits

The validator refuses mapping/dict subclasses, non-native keys, missing or extra fields, bool/float/string numeric substitutions, integer subclasses, non-finite values (not representable in this integer-only wire form), out-of-range quantities, wrong schema/classification, and inconsistent friction ordering. It never loads files, imports a physics engine, contacts a network, reads Noosphere, or mutates the input.

The fingerprint detects accidental content changes only. It is neither a signature nor authentication, and a caller-supplied `PUBLIC_SYNTHETIC` label is not proof of source provenance. The profile does not describe anisotropy, weave direction, plasticity, temperature dependence, tearing, contact geometry, collision behavior, or solver-specific stability. Mapping to MuJoCo/Jolt/PBD remains an independently reviewed adapter responsibility.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_cloth_material.py' -v
```
