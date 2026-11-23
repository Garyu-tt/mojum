# GENESIS bounded ledger JSON codec R0.1

```python
from mojum.genesis import GenomeLedger
from mojum.genesis_codec import dumps, loads

ledger = GenomeLedger(seed_fixture)
payload = dumps(ledger)          # canonical UTF-8 bytes
restored = loads(payload)        # validates schema, events and revision
assert restored.revision == ledger.revision
```

## Contract

- Reads and writes only the existing `mojum.genesis.ledger/1` fixture schema.
- Encodes canonical UTF-8 JSON and returns bytes; identical ledger revisions
  produce byte-identical output.
- Rejects duplicate object keys at every nesting level, invalid UTF-8, UTF-8
  BOM, malformed JSON, non-finite numeric constants, empty input, non-object
  roots and payloads larger than 1 MiB.
- Restores through `GenomeLedger.from_snapshot`, preserving the existing
  GENESIS schema, event validation, event-count bound and content-revision
  verification. This codec adds no parallel interpretation of the wire format.
- Uses only the Python standard library and is offline/CPU-only.

## Security and scope limits

This is a **local synthetic-fixture interchange codec**, not a durable database,
Noosphere adapter, replication protocol, authentication mechanism, signature,
encryption layer or privacy-preserving export. A content revision detects
accidental or unauthenticated modification only when compared with a trusted
expected value; it does not prove who created a snapshot. Do not use it to
export PRIVATE histories or credentials.

The 1 MiB cap applies to encoded input/output bytes, not process-wide memory.
Python's JSON decoder still allocates objects below that cap. The codec does
not add a new event-count or schema version and does not migrate old records.

## Verification

Run the isolated regression module:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_genesis_codec.py' -v
```

The repository's full CI matrix and independent review remain required before
this draft is treated as accepted or merged.
