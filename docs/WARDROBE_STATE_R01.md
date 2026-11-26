# Wardrobe state contract R0.1

This is an opt-in, offline, metadata-only wardrobe transition primitive for
PUBLIC_SYNTHETIC fixtures. It is not a cloth solver, garment importer, fit
validator, contact model, renderer, or production organism integration.

## State and authority

A state is bound to one caller-supplied body/rig revision. Its canonical
SHA-256 revision covers the schema, provenance, sequence, equipped
(slot, garment ID) pairs, and bounded recent operation IDs. The digest is a
consistency fingerprint, not an authentication signature.

The caller is responsible for selecting garment IDs from an independently
validated asset catalog. This module does not load or inspect assets.

## Transitions

- `equip`: slot must be empty and garment must not already be equipped.
- `unequip`: slot must contain the named garment.
- `drop`: same metadata removal contract as unequip; no simulated trajectory.
- `replace`: slot must be occupied and replacement must be a different,
  currently unequipped garment.

Every accepted transition requires an exact expected state revision and exact
body revision. It advances one logical sequence and returns a deterministic
receipt. Failed transitions do not mutate the frozen input state. Reusing an
operation ID within the retained 32-operation replay window is rejected.

## Limits and non-claims

Python 3.11+, standard library only. Slot vocabulary and item/history counts
are bounded. The operation history is a recent replay guard, not an unlimited
deduplication database; IDs older than the retained window may be reused.
No persistence, network, filesystem, SQLite, PRIVATE Noosphere, real device,
cloth deformation, calibrated fit, or physical contact is implemented.
