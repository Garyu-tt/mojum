# GENESIS acquisition attribution audit R0.1

This optional, read-only diagnostic decomposes a **PUBLIC_SYNTHETIC**
GENESIS ledger snapshot at a requested logical tick.

## Run

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis_attribution.py' -v
```

Example:

```python
from mojum.genesis import GenomeLedger
from mojum.genesis_attribution import audit_attribution

ledger = GenomeLedger(seed)
ledger.append(acquisition, ledger.revision)
report = audit_attribution(ledger.snapshot(), tick=12)
```

The report separates the immutable inherited seed values from the sum and
count of acquired events whose valid_from_tick is at or before the requested
tick. Later events are counted separately. The calculated values are checked
against the canonical GenomeLedger.phenotype(tick) result before a report is
returned.

## Boundaries

- No writes, migrations, SQLite access, Noosphere access or Aquarium wiring.
- No event IDs, source strings or evidence strings are copied into the report.
- Input is bounded by UTF-8 size, nesting depth, node count, text size and the
  existing 1,024-event GENESIS ledger limit.
- The digest is a deterministic content checksum, **not** a signature,
  authentication proof, provenance verification or protection against a
  malicious snapshot author.
- This is arithmetic attribution over the fixture schema, not proof of
  biological inheritance, development, learning or causal efficacy.
- No existing GENESIS schema or journal history is changed.
