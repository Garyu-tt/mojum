# R0.1 source-disjoint PUBLIC dataset splits (#950, parent #8)

A standalone **offline stdlib** tool for building reproducible train,
validation and test partitions from a previously validated
`mojum.dataset-view/1` projection. It neither reads SQLite, trains a model,
accesses host network, nor introduces a default runtime dependency.

## Example

```python
from mojum.dataset_view import project_episodes
from mojum.dataset_split import split_public_view, verify_public_split

public = project_episodes(
    [{"id": "s1", "source": "synthetic-room-A", "tick": 1,
      "observation": "toy ray", "visibility": "PUBLIC"}],
    allowed_sources={"synthetic-room-A"},
)
parts = split_public_view(public, seed="benchmark-r01")
assert verify_public_split(public, parts)
```

## Contract

- All records of the **same source** go in **one** partition. Source is
  assigned from SHA256 of versioned canonical JSON
  `[schema, assignment, seed, source]`, reduced modulo 100. Fixed percentages
  default to 80/10/10. Assignment is stable under append, record order and
  additional sources.
- Source-view digest, sorted records, strict UTF-8, identity uniqueness,
  timestamps and projected 16 MiB budget are revalidated through canonical
  `project_episodes`. Additional fields (including `visibility`, PRIVATE
  source events, permissions or arbitrary observations) are rejected rather
  than silently exported. No raw input journal reader is present.
- Inputs are bounded to 10,000 projected records and a UTF-8 seed of at most
  256 bytes. Results are **detached** from the input. Each partition preserves
  the PUBLIC view schema, computes its own checksum, and is included in a
  versioned checksummed manifest. `verify_public_split` recomputes all
  outputs from the original view and refuses modified/reordered material.
- Hashes attest **reproducibility**, not consent, trust, signer identity or
  authenticity. A forged but self-consistent PUBLIC view remains possible
  without a separately authenticated Noosphere source.
- Small datasets (especially one source) can yield empty validation/test
  partitions. Do not claim statistical balancing or model quality. Different
  source IDs may still describe correlated/duplicate real-world content, so
  this cannot guarantee complete semantic leakage prevention.
- Never feed PRIVATE / RESTRICTED journal entries directly to this module.
  An independently authorized PUBLIC projection remains a prerequisite.

## Test

```sh
PYTHONPATH=src python -m unittest tests.test_dataset_split -v
```

No changes to existing canonical journal, Aquarium or training code.
