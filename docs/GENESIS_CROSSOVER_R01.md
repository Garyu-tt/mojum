# GENESIS R0.1: deterministic two-parent seed crossover

## Purpose

`mojum.genesis_crossover` is an explicit, offline, stdlib-only transform for two caller-supplied `mojum.genesis.genotype/1` seed records. It chooses one inherited value from parent A or B independently for each supported gene, and returns a normal seed accepted by `GenomeLedger`.

This is a synthetic inheritance fixture. It is **not** biological reproduction, an evolutionary simulation, neural learning, a trained organism, or proof of cognition.

## Reproduction

```python
from mojum.genesis import GenomeLedger
from mojum.genesis_crossover import cross_genomes

result = cross_genomes(
    parent_a,
    parent_b,
    child_genome_id="child-001",
    random_seed=20261123,
)
child_seed = result.seed  # fresh detached dict
child = GenomeLedger(child_seed)
print(result.to_dict())
```

## Contract

- Both parents must be exact native dictionaries with the exact genotype schema, identity/revision strings and complete current `GENES` inventory.
- Gene values are checked by the existing canonical GENESIS validator; booleans, floats, non-finite values, unknown genes and out-of-range parameters are rejected.
- Parent genome IDs must differ. The child ID must be a bounded ASCII identifier distinct from both parents.
- The caller supplies an unsigned 64-bit integer seed. `bool` is rejected even though it subclasses `int`.
- For each gene in sorted order, a domain-separated SHA-256 selector uses the seed, ordered parent seed digests and gene name. Its low bit selects exactly one parent's value. Parent order is meaningful and preserved in provenance.
- The report records each selected parent and value, both parent IDs/revisions/seed digests, the child seed and a stable SHA-256 consistency digest. It does not copy acquisition events or parent histories.
- The frozen result stores canonical JSON internally; properties return fresh detached dictionaries/lists. The child seed can be passed directly to `GenomeLedger`.
- No randomness outside the supplied seed, filesystem, network, SQLite, Noosphere, Aquarium, Graph IR, external dependencies or motor authority are used.

## Limits and interpretation

The implementation only combines three bounded integer fixture parameters defined by the existing GENESIS seed contract. There are no dominance, linkage, mutation, recombination-rate, sex, population, developmental, fitness or selection semantics. A digest is a consistency fingerprint, not authentication or proof of parent identity. Parent IDs/revisions are caller-provided labels, not signed identities.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis_crossover.py' -v
```
