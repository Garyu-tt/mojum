# Noosphere PUBLIC Dataset Export Witness R0.1

## Purpose

`mojum.noosphere.public-export-witness/1` is a bounded, read-only receipt for one
reproducible projection created by the existing `LocalNoosphere.dataset()`
API. It records the source journal's internally verified event count and hash
head, the requested filter/limit set, the number of exported rows, and the
SHA-256 digest of the exact PUBLIC projection.

The helper lives outside the journal implementation and does not change event
schemas, SQLite tables, chain encoding, or the existing dataset API.

## Example

```python
from mojum.noosphere import LocalNoosphere
from mojum.noosphere_export_witness import create_public_export_witness

with LocalNoosphere("./dante.sqlite3", "dante-synthetic", read_only=True) as store:
    receipt = create_public_export_witness(
        store,
        filters={"topic": "heartbeat", "valid_at_ns": 1_000_000},
        limit=500,
        expected_head="<independently retained lowercase SHA-256 head>",
    )
print(receipt["receipt"])
print(receipt["sha256"])
```

The helper starts a SQLite read transaction, verifies the full event chain and
optional external head, requests a PUBLIC-only projection, validates the
projection envelope and digest, then commits the read transaction. Concurrent
appends cannot mix rows from a later journal state into this receipt. The
recorded `source_head` identifies the exact snapshot represented.

## Receipt fields

- `source_count` and `source_head`: verified event count and terminal chain
  digest from the same SQLite read snapshot.
- `external_anchor_verified`: true only when the caller supplied an external
  lowercase SHA-256 head and it matched.
- `filters`: exact topic/source/time filters plus the enforced
  `include_nonpublic=false` and requested row limit.
- `exported_count` and `projection_sha256`: number and digest of projection
  rows actually selected by the existing dataset API.
- Outer `sha256`: deterministic digest of the receipt fields.

## Safety and limitations

- The helper does not accept `include_nonpublic`; PRIVATE and RESTRICTED
  records are never included by this export helper.
- It does not copy or print event payloads. The caller must obtain the actual
  projection separately through the established Noosphere API.
- A matching chain head without an independently retained anchor proves only
  internal consistency, not resistance to a local attacker rewriting both
  journal and head.
- SHA-256 is an unkeyed reproducibility digest, not a signature, identity proof,
  access-control mechanism, consent record, retention policy, or proof that
  public disclosure is authorized.
- No encryption, replication, training, network, journal mutation, migration,
  production runtime wiring, or PRIVATE data access is implemented.
- Requires an idle `LocalNoosphere` connection so the helper can own and
  consistently delimit its SQLite read transaction.

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_noosphere_export_witness.py -v
```

All fixtures are synthetic and use disposable temporary SQLite files.
