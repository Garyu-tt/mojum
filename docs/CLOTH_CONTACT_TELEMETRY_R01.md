# Cloth contact telemetry reducer R0.1

## Purpose

This opt-in utility summarizes **caller-supplied PUBLIC_SYNTHETIC measurements** of
cloth contact. It is a data-quality and telemetry boundary, not a cloth solver,
sensor driver, collision detector, or proof of physical contact.

It produces fixed category aggregates for `body`, `hand`, and `world`.
It does not retain or return individual samples, positions, mesh data, garment
identity, user content, or private journal records.

## API

```python
from mojum.cloth_contact_telemetry import reduce_contacts

report = reduce_contacts([
    {"tick": 1, "contact": "body", "normal_force_mn": 120, "strain_ue": -9},
    {"tick": 1, "contact": "hand", "normal_force_mn": 40, "strain_ue": 15},
])
```

All fields are integer-domain values:
- `tick`: nonnegative simulation tick, monotone nondecreasing.
- `contact`: one of `body`, `hand`, `world`.
- `normal_force_mn`: nonnegative milli-newtons.
- `strain_ue`: signed microstrain.

`dt_ns` is an explicit fixed logical step supplied by the caller. The default
is 10 ms; accepted bounds are 1 µs through 1 s. Tick equality is allowed for
multiple samples in one step. The reducer never reads wall-clock time.

## Safety and resource bounds

- stdlib-only, CPU-only and offline; no imports of MuJoCo/Jolt/PBD.
- 2,048 samples per call; fixed numeric ranges and explicit aggregate ceiling.
- Exact native Python types; booleans are not integers for this protocol.
- Exact sample keys; unsupported visibility/classification fails closed.
- Stable canonical SHA-256 is an integrity/determinism receipt, **not a
  signature, authenticity proof, or source identity**.
- No disk, network, SQLite, Noosphere, device, motor, or runtime side effects.

## Limitations

Values are measurements asserted by the caller. This module does not check
whether a force sensor exists, whether units are calibrated, whether values
match MuJoCo contacts, whether a garment is actually worn, or whether geometry
is safe. It computes category-level counts, force sums/peaks and maximum
absolute strain only. It cannot infer missing samples or physical causality.

The feature is not wired into Aquarium and does not enable cloth physics.
