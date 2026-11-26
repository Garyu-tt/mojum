# Neural snapshot guard R0.1

\`mojum.neural_snapshot_guard.restore_snapshot(state)\` is an opt-in trust-boundary helper for explicit synthetic \`mojum.neural.delayed-pulse/1\` snapshots.

It rejects non-native dict/list/string subclasses, non-string keys, cycles/shared containers, excessive nesting/node counts, oversized integer scalars, non-finite floats, unexpected fields and unsupported summation names before invoking the existing \`PulsePredictor.from_snapshot\` semantic checks. The accepted snapshot is detached before restore, so later caller mutation cannot alter predictor state.

## Scope and non-claims

- CPU-only Python standard library; no new mandatory dependencies.
- Does not change \`neural.py\`, historic checkpoint bytes, Aquarium replay or journal schema.
- Does not authenticate provenance, authorize PRIVATE data, encrypt state, or make a checksum/signature claim.
- Does not execute learning, access SQLite, touch hardware or emit motor commands.
- Limits are for the 7-field predictor checkpoint, not a generic JSON parser.

## Test

\`\`\`sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_neural_snapshot_guard.py' -v
\`\`\`
