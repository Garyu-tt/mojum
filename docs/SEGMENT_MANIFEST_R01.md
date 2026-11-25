# Physical segment manifest contract R0.1

Status: experimental, additive, PUBLIC_SYNTHETIC fixtures only.

## Purpose

Define a small immutable metadata contract for a future physical Noosphere segment bundle. A manifest identifies one contiguous event-sequence range, the source event schema, the SHA-256 digest of the segment bytes, and the preceding segment head. The implementation is deliberately a pure Python standard-library component.

It is **not** a physical archive writer, crash-safe rollover protocol, encryption system, signature scheme, anti-rollback anchor, replica protocol, quorum, or production Noosphere migration. A SHA-256 digest detects accidental or untrusted changes only when the expected digest/head is independently trusted.

## Wire schema

Schema identifier: `mojum.noosphere.segment-manifest/1`.

Exactly these JSON fields are required:

| Field | Meaning |
|---|---|
| `agent_id` | Stable organism identifier |
| `first_seq`, `last_seq` | Inclusive global event sequence range, starting at 1 |
| `event_count` | Must equal `last_seq - first_seq + 1` |
| `previous_segment_head` | Previous segment head; all-zero digest only for genesis |
| `segment_head` | Domain-separated SHA-256 over canonical chain fields |
| `source_schema` | Event schema represented by the segment |
| `content_sha256` | SHA-256 of the exact physical segment bytes |
| `origin_device` | Caller-supplied non-secret provenance label |
| `created_at_ns` | Caller-supplied nonnegative timestamp |

The segment head hashes a canonical, sorted-key, compact UTF-8 JSON object containing schema, agent identity, sequence range/count, previous head, source schema and content digest. Origin label and creation time remain manifest metadata and do not alter the chain head. The complete canonical manifest has a separate `manifest_sha256` consistency digest.

## Validation and limits

- Python 3.11+, standard library only; no mandatory dependency.
- Exact native dictionaries, strings, integers and bytes; booleans are not integers for this contract.
- Sequence ranges are contiguous and nonempty. Genesis starts at sequence 1 and references the empty head; later segments require a nonempty previous head.
- SHA-256 fields are exactly 64 lowercase hexadecimal characters.
- Canonical JSON is UTF-8, sorted-key and compact; duplicate keys and alternate encodings are rejected.
- Maximum encoded manifest size is 16 KiB; text labels are at most 256 characters.
- Text length is admitted before scanning its contents. Canonical serialization and content verification revalidate an exact `SegmentManifest` snapshot; incomplete records and inaccessible wire buffers raise `SegmentManifestError`.
- Segment content verification accepts exact `bytes` supplied by the caller, capped at 64 MiB before hashing, and performs no filesystem or network access.
- Content verification compares against the validated snapshot even if the caller's original record is mutated while hashing.

## Example

```python
from hashlib import sha256
from mojum.segment_manifest import EMPTY_HEAD, create_manifest

payload = b"PUBLIC_SYNTHETIC fixture events"
manifest = create_manifest(
    agent_id="fixture-organism",
    first_seq=1,
    last_seq=3,
    previous_segment_head=EMPTY_HEAD,
    source_schema="mojum.noosphere.event/1",
    content_sha256=sha256(payload).hexdigest(),
    origin_device="fixture-node-a",
    created_at_ns=1,
)
assert manifest.event_count == 3
```

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_segment_manifest.py' -v
```

No claim is made about durable file writes, independent replica survival, encryption at rest, measured resource use on Android, or distributed failover.
