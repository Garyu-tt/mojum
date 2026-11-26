# R0.1: strict synthetic rig DOF limits

## Purpose

`mojum.rig-dof-limits/1` is an opt-in, stdlib-only metadata contract for
validating a caller-supplied ordered inventory of virtual rig degrees of
freedom. It is intentionally detached from the Aquarium, SQLite journals,
Universal Graph IR, asset loading and any actuator path.

## Input

The top-level object has exactly `schema`, `rig_id`, `rig_revision`, and
`dofs`. Each DOF has exactly `id`, `kind`, `unit`, `axis`, `lower`,
and `upper`.

- `kind=revolute` requires `unit=rad`; `kind=prismatic` requires `unit=m`.
- Axes are three finite numeric components and must already be unit length
  within 1e-6. The validator does not normalize axes.
- Limits are finite, non-boolean numbers with strict `lower < upper`.
- IDs are exact ASCII identifiers, unique within the ordered inventory.
- The inventory is bounded to 1..128 DOFs; revisions are non-negative 63-bit
  exact integers. Unknown fields and unsupported joint kinds fail closed.

## Output and digest

The validator returns frozen detached dataclasses. `to_dict()` returns a new
JSON-compatible structure. The SHA-256 digest covers the canonical schema,
rig identity/revision and ordered normalized declaration. Reordering DOFs,
changing units, limits, axes or revision changes the digest.

## Explicit non-goals

This checks declaration consistency only. It does not prove that a rig exists,
that limits match anatomy, that axes match a mesh, or that values are safe for a
real body. It performs no FK/IK, dynamics, collision, torque or motor checks.
No units are inferred, converted or silently repaired. No network, external
dependency, private data, database or production device is accessed.

## Tests

Run from repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_rig_dof_limits.py' -v
```

The suite covers deterministic digesting, detached immutable output, strict
schema/type/ID/unit/axis/range gates, count budgets and adversarial malformed
inputs. Run the repository's full CPython 3.11–3.13 CI and independent review
against the exact PR head before considering this slice accepted.
