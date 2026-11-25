# Cloth contact telemetry reducer R0.1

## Purpose and boundary

`mojum.cloth.contact-telemetry/1` is a bounded, offline, read-only reducer for
explicitly supplied **PUBLIC_SYNTHETIC** per-contact measurements. It
summarises normal force, tension and compression for the declared `body`,
`hand` and `world` surfaces.

This is a data-contract utility, not a cloth solver or sensor driver. It does
not generate contact samples, infer missing contact, inspect geometry, validate
physical realism, command actuators, write an Aquarium/Noosphere journal, or
retain raw source data after the call. A successful report proves only that
the supplied values passed this schema and aggregation contract.

## API

```python
from mojum.cloth_contact_telemetry import PUBLIC_SYNTHETIC, reduce_contacts

report = reduce_contacts(
    [{
        "tick": 1,
        "observed_at_ns": 1_000_000,
        "rig_revision": "body-r01",
        "contact_id": "scarf-edge-1",
        "surface": "hand",
        "normal_force_n": 0.4,
        "tension_n": 0.2,
        "compression_n": 0.0,
    }],
    rig_revision="body-r01",
    provenance=PUBLIC_SYNTHETIC,
)
print(report.to_dict())
```

The call requires an explicit provenance declaration and an exact rig revision.
All sample records must be plain dictionaries with exactly the documented
fields. Tick and observed-at clocks are non-decreasing; same-tick samples may
share timestamps, but `(tick, contact_id)` must be unique. Input order is
preserved and never silently sorted.

## Limits and units

- Python 3.11+, standard library only; no runtime dependency or network access.
- At most 4,096 records per call.
- Ticks and timestamps are native non-negative 63-bit integers; timestamp unit
  is nanoseconds.
- Forces are finite native integer/float values in Newtons, from zero through
  1,000,000 N inclusive. Booleans are not numbers in this contract.
- Contact identifiers and rig revisions use bounded ASCII identifier forms.
- Only `body`, `hand`, and `world` are accepted as surfaces.
- Values outside the contract fail closed with `ContactTelemetryError`.

The reducer does not claim that 1,000,000 N is a safe physical threshold. It is
an input ceiling to bound the synthetic data contract and catch unit mistakes.
The module has no calibration data and no body-specific safety envelope.

## Determinism and integrity

Samples are normalized to native numeric values and hashed as canonical JSON
with SHA-256. The report contains that input digest and a second digest over the
complete report body. Aggregates use `math.fsum` and a fixed surface order.
The frozen dataclasses and tuple summaries are immutable; `to_dict()` returns
a fresh detached JSON-compatible object.

Digests detect accidental content changes when independently recomputed. They
are not signatures, authentication, proof of source consent, replay protection,
or trusted timestamps.

## Integration gates

Keep this module opt-in and disconnected from the canonical runtime until
there is a reviewed physical adapter with measured units, calibration,
coordinate-frame and rig-revision contracts. Future integration must also
define contact sampling cadence, duplicate aggregation semantics, contact
identity lifetime, penetration/normal conventions, force filtering and
provenance review. A synthetic telemetry report must never be presented as a
MuJoCo/Jolt/PBD simulation result or as validated tactile perception.

No third-party assets, source meshes, personal data, private journals, or
hardware were used for this implementation.
