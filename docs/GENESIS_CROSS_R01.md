# GENESIS synthetic founder crossing R0.1

## Purpose

`mojum.genesis_cross` implements an explicit, deterministic fixture operation over two existing GENESIS v1 seed snapshots. It chooses one inherited value from parent A or parent B independently for each current fixture gene. The operation does not blend values or alter acquired-event ledgers.

This is a software-test fixture operator only. It is not biological reproduction, real DNA, a trained organism, cognition, or evidence of evolution.

## API

```python
from mojum.genesis_cross import cross_seeds, verify_cross_receipt

result = cross_seeds(
    parent_a_seed,
    parent_b_seed,
    cross_seed=123,
    child_genome_id="synthetic-child-001",
)
child_seed = result["child_seed"]
receipt = result["receipt"]
assert verify_cross_receipt(result)
```

Both parents are validated by the canonical `GenomeLedger` v1 seed validator. Parent order is significant. `cross_seed` is a caller-supplied native integer in the signed 63-bit nonnegative range. A SHA-256 bit over the version, seed, gene name and ordered parent content hashes selects the source parent. The algorithm does not use Python's global RNG or `random.Random` implementation details.

The child keeps the existing `mojum.genesis.genotype/1` wire shape and receives a fresh caller-provided identity. Its revision is derived from the cross inputs and inherited values. The receipt records parent content digests, per-gene selections and the child digest.

## Boundaries

- Pure stdlib and CPU-only; no I/O, network, persistence or external dependencies.
- Parent snapshots and acquired ledgers are never mutated.
- The generated child is revalidated by the canonical GENESIS v1 validator.
- The receipt digest is a consistency checksum, not a signature or authorization proof.
- `verify_cross_receipt` verifies the self-contained receipt and child consistency. It cannot authenticate the parents or prove that the receipt came from a trusted actor.
- No changes to canonical GENESIS schema, Aquarium, SQLite or Noosphere behavior.
- Every current gene is inherited from exactly one parent; there is no mutation, averaging or hidden random state.

## Tests

Run the focused suite:

```sh
PYTHONPATH=src python -m unittest -v tests.test_genesis_cross
```

The suite covers deterministic replay, v1 seed compatibility, parent immutability, bounds, malformed parent values, identity collisions and receipt tampering.
