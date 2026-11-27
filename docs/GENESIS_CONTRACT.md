# GENESIS CPU-first contract — isolated R0.1

Scope: fixture-only genotype to phenotype compilation, not anatomy, biology, neural training, or real genetic reproduction. Developed for [#4](https://github.com/Garyu-tt/mojum/issues/4).

- **Immutable inherited seed** with three bounded, integer-only placeholder genes: synthetic heartbeat BPM, body scale in thousandths, sensory gain in thousandths.
- **Acquired event ledger** append-only in memory. Every event includes source/evidence, observed and recorded virtual nanoseconds, valid-from tick, unique event ID, one targeted integer delta.
- **Revision/CAS gate** prevents blind concurrent overwrites. `revision` is a SHA-256 *content checksum*, not a signature or security authorization.
- **Phenotype projection** at a tick compiles effective gene parameters without mutating the seed. Historic queries reflect the *current ledger's* event collection; previous historical views are not immutable until a persistent provenance/version store is implemented.
- **Safety guards:** reject unknown genes/keys, float/NaN/bool, revision mismatch, bad causal timestamps, duplicate IDs and any event that makes the phenotype violate limits at *any* valid-from boundary.
- **Snapshot** makes a deep copy and checks revision on reconstruction. No atomic cross-module checkpoint and no disk/Noosphere persistence in this version.

## Local test

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis.py' -v
```

## Integration roadmap

The existing `genesis.seed` graph fixture in PR #3 is deliberately separate; connect this phenotype service only after `mechanicus.universal-graph/1` definition contracts from PRs #2/#3 are reconciled. The first connection should map `heartbeat_bpm` into `genesis.seed` via a **typed, revision-matched** port. Later attach real developmental mappings and acquired changes to GENESIS, Noosphere, and Dante's physical rig.
