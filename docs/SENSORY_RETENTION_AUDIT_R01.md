# Sensory retention audit R0.1

`mojum.sensory-retention-audit/1` is an offline, read-only validator for the
existing `mojum.sensory-curiosity.association/2` synthetic contact-association
snapshot. It deliberately does not call the model loader, open the Aquarium
SQLite journal, change retention state, or perform a schema migration.

## Checks

- Exact source schema and exact builtin container/scalar types.
- At most 128 retained numeric RGB cues and at most 100,000 cumulative updates.
- Every cue has a bounded canonical RGB key, positive observation count, and a
  finite mean deflection in `[0, 1]`.
- A unique in-range last-measured tick per retained cue.
- `sum(retained cue counts) + forgotten_observations == updates`.
- Evictions are bounded by the number of updates beyond the 128-cue capacity,
  and each eviction has at least one forgotten observation.
- Deterministic oldest-first LRU age summary and SHA-256 of canonical snapshot
  bytes, with no raw cue values in the returned report.

## Usage

```python
from mojum.sensory_retention_audit import audit_snapshot

report = audit_snapshot(snapshot)
```

Invalid, legacy, malformed, or internally inconsistent snapshots raise
`RetentionAuditError`. The returned SHA-256 is a content identifier, **not** an
authentication signature or proof of origin. This auditor cannot prove that
measurements really occurred, that eviction history was honest, or that a
snapshot came from the owner. It validates only internal evidence consistency
in a caller-supplied `PUBLIC_SYNTHETIC` fixture. It does not process PRIVATE
Noosphere data, drive sensors/motors, or claim real perception or cognition.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_sensory_retention_audit.py' -v
```

The tests are hermetic and use only Python's standard library.
