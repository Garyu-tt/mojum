# Rig inertial metadata R0.1

## Purpose

`mojum.rig_inertia` validates a small, caller-supplied inventory of rigid-link
inertial properties. It is an offline metadata boundary for explicitly
`PUBLIC_SYNTHETIC` fixtures, not a body model, calibrated anatomy, or physics
engine integration.

## Wire contract

The input is an exact plain-Python dictionary with these fields:

- `schema`: `mojum.rig-inertia/1`
- `scope`: `PUBLIC_SYNTHETIC`
- `unit_system`: `SI`
- `links`: 1..256 exact dictionaries

Each link has exactly `id`, `mass_kg`, `com_m`, and `inertia_kg_m2`.
IDs are unique bounded ASCII tokens. Mass is strictly positive and at most
1,000,000 kg. The center of mass is a finite three-vector bounded to ±1,000 m.
The inertia tensor is a finite 3×3 matrix in kg·m² with entries bounded to
±1e12.

The tensor must be exactly symmetric and positive definite (Sylvester's
criterion). Its three principal moments must also satisfy the rigid-body
triangle inequality: the largest cannot exceed the sum of the other two.
This is a necessary physical-consistency check, not proof that the values came
from a realizable mesh or measured object.

## API

```python
from mojum.rig_inertia import validate_rig_inertia

result = validate_rig_inertia(payload)
print(result.sha256)
for link in result.links:
    print(link.link_id, link.mass_kg, link.center_of_mass_m)
```

The result is immutable and detached from the caller's containers. Link order
is normalized by ID before the SHA-256 consistency digest is calculated.
The digest is not a signature, authentication token, or anti-rollback proof.
`result.as_dict()` returns a fresh JSON-compatible copy.

## Verification

Run the focused offline tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_rig_inertia.py' -v
```

No third-party dependency, filesystem access, network, SQLite/Noosphere,
Aquarium integration, MuJoCo execution, mesh parsing, inference, or actuator
authority is used.

## Explicit limitations

- Caller-supplied values and the `PUBLIC_SYNTHETIC` label are untrusted
  assertions. This validator does not authenticate provenance or consent.
- It does not compute mass properties from geometry or infer missing values.
- Passing the mathematical checks does not calibrate anatomy, prove contact,
  guarantee dynamic stability, or establish physical safety.
- The module is opt-in and is not wired into the canonical rig, physics
  runtime, graph schema, or persisted journal.
