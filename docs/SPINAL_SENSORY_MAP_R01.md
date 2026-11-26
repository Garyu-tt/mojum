# Bounded spinal sensory routing map R01

Status: additive `PUBLIC_SYNTHETIC` CPU/stdlib contract.  The implementation is
`src/mojum/spinal_sensory_map.py`; it is not connected to a live runtime.

## Claim boundary

This component validates caller-supplied metadata that associates a named
surface region with one or more logical routes.  It provides deterministic map
digests and immutable routing receipts.  It does **not**:

- establish biological dermatome or cranial-nerve accuracy;
- detect, localize, or authenticate a physical contact;
- read FingerSense, USB, haptic, camera, or other hardware;
- infer object, material, pose, or privileged world information;
- integrate with MotorGate, Aquarium, Noosphere, MuJoCo, or the spinal runtime;
- make a digest into a signature, proof of origin, or consent mechanism.

The 31 spinal identifiers (`C1..C8`, `T1..T12`, `L1..L5`, `S1..S5`, `Co1`)
match the logical engineering topology proposed by the spinal foundation.  The
agreement is identifier-level only and does not claim anatomical fidelity.

## Map admission

`compile_sensory_map()` accepts an exact native dictionary:

```json
{
  "schema": "mojum.spinal-sensory-map/1",
  "visibility": "PUBLIC_SYNTHETIC",
  "consent": "explicit",
  "map_id": "fixture-surface-map",
  "revision": 3,
  "regions": [
    {
      "region_id": "hand.left",
      "pathway": "spinal",
      "routes": ["C6", "C7"],
      "density_ppm": 900000,
      "lod": 5
    },
    {
      "region_id": "head.face",
      "pathway": "cranial",
      "routes": ["cranial:face.primary"],
      "density_ppm": 700000,
      "lod": 3
    }
  ]
}
```

Spinal regions accept only the 31 identifiers above.  Cranial regions accept
only bounded ASCII route IDs in the separate `cranial:` namespace.  A region
may have 1..8 unique routes; different regions may intentionally share a
spinal segment.  Density (`1..1,000,000` parts per million) and LOD (`0..7`)
are opaque caller-declared metadata.  They do not alter signal strength.

The complete map is bounded to 256 regions, 1,024 route assignments, and
131,072 canonical JSON bytes.  Identifiers contain at most 96 restricted ASCII
characters.  Unknown fields, container subclasses, booleans in integer fields,
mixed spinal/cranial namespaces, duplicates, and out-of-range values fail
closed.  Region order and route order are canonicalized before SHA-256 hashing.

## Contact routing

`route_contact()` admits one exact-shape event:

```json
{
  "schema": "mojum.spinal-sensory-contact/1",
  "visibility": "PUBLIC_SYNTHETIC",
  "consent": "explicit",
  "map_id": "fixture-surface-map",
  "map_revision": 3,
  "event_id": "contact-0001",
  "tick": 42,
  "source_id": "synthetic-contact-fixture",
  "source_revision": 7,
  "region_id": "hand.left",
  "signal_ppm": 125000
}
```

The map ID and revision must match the compiled map, and the region must exist.
Signal is a bounded opaque integer in `0..1,000,000`; no physical unit or
calibration is implied.  Coordinates, object IDs, material labels, and arbitrary
payloads are outside this contract and are rejected rather than trusted.

The returned frozen receipt records map digest/revision, event and source
provenance, canonical routes, density, LOD, and signal.  Its SHA-256 digest is
deterministic for the same admitted map and event.  Caller dictionaries and
lists are copied into immutable tuples, so later mutation cannot change a map
or receipt.

## Verification

Focused validation:

```sh
PYTHONPATH=src python -B -W error::ResourceWarning -m unittest \
  tests.test_spinal_sensory_map -v
```

The suite covers overlapping routes, spinal/cranial separation, all 31 logical
segments, canonical ordering, revision and provenance binding, immutable
detachment, exact shapes/native types, hostile container subclasses, namespace
confusion, identifier and integer boundaries, cardinality/byte budgets, and
rejection of privileged world fields.
