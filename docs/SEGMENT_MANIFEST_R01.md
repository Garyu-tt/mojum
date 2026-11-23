# Physical Segment Manifest R0.1

Status: opt-in metadata contract and offline validator prototype. This is not a
physical archive writer, durable journal, replication protocol, or backup.

## Purpose

Define a small independently inspectable metadata record for one immutable
physical event segment. The implementation is Python 3.11+ standard-library
only and accepts caller-supplied metadata. It performs no filesystem, network,
SQLite, encryption, signing, key-management, or device operations.

## Wire schema

Schema identifier: `mojum.physical-segment-manifest/1`.

Required fields:

| Field | Meaning |
|---|---|
| `agent_id` | Bounded opaque agent identifier |
| `first_seq`, `last_seq` | Inclusive global sequence interval |
| `event_count` | Must equal `last_seq - first_seq + 1` |
| `previous_segment_head` | Previous manifest head, or null for the first segment |
| `segment_head` | SHA-256 of canonical UTF-8 JSON for every field except itself |
| `source_schema` | Source event schema identifier |
| `content_sha256` | SHA-256 of the segment content bytes, calculated by the caller |
| `origin_device_id` | Opaque device pseudonym, not a hardware identifier |
| `created_at_ns` | Caller-supplied Unix timestamp in nanoseconds |

The validator requires the exact field set and exact native integer/string
types. Booleans are not accepted as integers. Digests must be lowercase
64-character hexadecimal strings. Sequence ranges are inclusive, non-empty,
and bounded to signed 63-bit values. JSON parsing rejects duplicate keys,
invalid UTF-8, non-finite constants, and payloads over 16 KiB.

## Chain and trust model

The first segment uses a null previous head. Later segments point to the prior
manifest's head. This permits deterministic chain-consistency checking when a
caller supplies the ordered manifests. A plain SHA-256 chain is **not**
authentication: an attacker able to rewrite every segment and manifest can
recompute it. Production use still requires independently anchored roots,
authenticated manifests, key rotation, anti-rollback protection, and
authorization. None is implemented here.

The content digest is supplied by the caller. This module does not read bytes
or establish that the digest actually matches a stored segment.

## Crash/recovery boundary

This contract does not define append, rollover, fsync, atomic publication,
orphan quarantine, recovery, retention, or power-loss semantics. It does not
prove that a segment exists or is durable. Those are separate acceptance
gates under parent issue #141.

## Privacy and resource limits

Use only PUBLIC_SYNTHETIC fixtures. Device identifiers must be opaque
pseudonyms; never put hardware IDs, hostnames, paths, credentials, or private
journal content in manifests. No PRIVATE Noosphere records are read or exported.

The validator is bounded to a 16 KiB JSON wire record, 128-character opaque
identifiers, 160-character source schema, and signed-63-bit non-negative
sequence/timestamp values. No external dependencies are required.

## Verification

Run:

```sh
PYTHONPATH=src python -m unittest tests.test_segment_manifest -v
```

The test module covers deterministic construction and round-trip, chain links,
exact fields, strict integer typing, interval consistency, digest tampering,
duplicate JSON keys, non-finite values, wire-size limits, and detached results.
Exact-head repository CI and independent QA remain mandatory before merge.
