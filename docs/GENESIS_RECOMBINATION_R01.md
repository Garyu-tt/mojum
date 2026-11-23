# GENESIS deterministic two-parent recombination R0.1

**Status:** additive synthetic fixture contract. This module does not implement biological reproduction, meiosis, evolution, learned inheritance or cognition.

## Purpose

The current GENESIS seed contract has one inherited integer-valued genotype and an append-only acquired-change ledger. The somatic body documentation explicitly records that the inherited somatic chromosome is static and has no recombination. This opt-in module adds a deterministic way to construct a child genotype from two existing, independently validated GENESIS seeds without changing the v1 seed or ledger schemas.

## API

```python
from mojum.genesis_recombination import recombine, verify_recombination

result = recombine(
    parent_a,
    parent_b,
    child_genome_id="synthetic-child-001",
    starting_parent=0,
    crossover_after=(1,),
)
child_seed = result["seed"]
provenance = result["provenance"]
assert verify_recombination(result, parent_a, parent_b)
```

The example uses the current three-gene order in `mojum.genesis.GENES`. Crossover positions are zero-based gene indices after which the selected parent toggles. The crossover map and starting parent must be supplied explicitly. No random generator is hidden in the operation.

## Contract and limits

- Input parents must be native dictionaries satisfying the canonical `mojum.genesis.genotype/1` validator.
- Parent genome IDs must be distinct. Child ID must be a new non-empty UTF-8 identifier.
- Parent gene values retain the canonical integer ranges and are copied without mutation.
- The deterministic child seed keeps the existing GENESIS genotype schema. Its revision is derived from the canonical recombination provenance digest.
- Provenance records both ordered parent IDs and seed hashes, gene order, crossover map, starting parent, per-gene parent attribution, inherited values and a SHA-256 consistency digest.
- Verification recomputes the operation against the supplied parent seeds. The digest is a consistency check, **not** an authentication signature or proof of parent identity.
- The result is detached data. It does not update a parent, ledger, Aquarium, SQLite journal, Noosphere, body runtime or Graph IR.
- No biological calibration, mutation model, chromosome linkage model, fertility, physical embodiment or cognition claim is made.

## Verification

```sh
PYTHONPATH=src python -m unittest tests.test_genesis_recombination -v
```

The focused tests cover deterministic replay, crossover semantics, parent selection, input/output isolation, provenance verification, tampering, malformed types, invalid parent seeds, identifier policy and canonical gene bounds. The focused workflow tests CPython 3.11, 3.12 and 3.13. Full repository acceptance and independent review remain separate gates.
