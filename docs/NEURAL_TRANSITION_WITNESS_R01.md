# Neural transition witness R0.1

`mojum.neural_transition_witness.attest_transition()` independently checks one
state transition of the existing delayed-pulse `PulsePredictor` without
writing to the Aquarium, journal, or runtime.

## Contract

The caller supplies three detached JSON-shaped values: a predictor v1
`before_snapshot`, one exact interoception frame, and an `after_snapshot`.
The caller must explicitly assert `classification="PUBLIC_SYNTHETIC"` and
select `summation="ordered"` or `summation="compensated"`. The latter is
required because predictor v1 snapshots do not persist their floating-point
summation rule.

The frame is restricted to `kind`, `observed_at_ns`, and a heartbeat object
containing exactly `phase_milli`, `pulse`, and `synthetic=true`. Native types,
finite values, timestamp bounds, depth, object cardinality, UTF-8 and encoded
size are checked before replay. The before and after snapshots are validated
by the existing `PulsePredictor.from_snapshot` semantic contract. Replay uses
a deep detached copy and exactly one call to `observe()`.

The report contains state/frame/receipt digests, transition counters and the
names of mismatched snapshot fields. It never returns raw snapshots or the
sensory frame. A syntactically valid but incorrect post-state produces
`status=MISMATCH`; malformed inputs or rejected transitions raise
`TransitionWitnessError`.

## Run

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neural_transition_witness.py' -v
```

## Limits and non-claims

This is a deterministic consistency witness for one caller-supplied
PUBLIC_SYNTHETIC transition, not authenticated provenance, a durable audit
log, a model-quality evaluation, a neural science result, a privacy
authorization system, or proof of biological behavior. It does not read or
write SQLite/Noosphere, access network/files/devices, emit motor authority,
change predictor schemas, or add dependencies. Hashes are unkeyed checksums,
not signatures. Integration and release require independent exact-head CI
and review.
