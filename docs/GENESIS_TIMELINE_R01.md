# GENESIS synthetic phenotype-change timeline R0.1

Parent issue: #4. Independent additive fixture-only implementation; no runtime,
Noosphere, external model, motor output or database mutation.

~~~python
from mojum.genesis_timeline import attest_timeline
from mojum.genesis import GenomeLedger

ledger = GenomeLedger(synthetic_seed)
result = attest_timeline(ledger.snapshot(), start_tick=0, through_tick=100,
                         public_synthetic=True)
~~~

The API takes only an existing `GenomeLedger.snapshot()` and **replays and
verifies** the snapshot revision before producing any output. The caller must
explicitly certify synthetic/public input; the default is refuse. Outputs are
SHA-256-integrity-fingerprinted, **not signed**, and are not accepted as authority
for genotype updates, medical biology, training data, real actuation, or Noosphere
PRIVATE records.

At most 256 ordered timeline boundaries are returned: the inclusive start,
inclusive end and distinct `valid_from_tick` within the interval. At each
boundary the module reports parameter values, acquired deltas since the
previous boundary (for the first boundary: since inherited seed), count and
hash of effective acquisition IDs, and newly effective event IDs with gene,
delta, effective tick and observed/recorded timestamps. New events retain
append order even when their valid-from dates differ. Source and raw evidence
fields are **not copied**: each complete acquired event is represented by a
content hash. The report's own `sha256` hashes all other fields.

## Acceptance

Run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis_timeline.py' -v`.

Checks include out-of-order effective ticks, multiple events on the same tick,
window boundaries, immutability, repeatability, privacy suppression, forged
snapshot rejection, malformed tick types, explicit opt-in and density quotas.
The module is stdlib-only, importable on Python 3.11+, and requires no feature
flags in the live organism. It intentionally does not emit every tick across
a long interval. A boundary digest cannot prove that timestamps, event source
assertions or observations were true, only that they are the validated content
of a particular ledger revision.
