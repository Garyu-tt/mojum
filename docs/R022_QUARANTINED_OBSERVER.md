# Mojum R0.22 — historical Aquarium observer (owner-HMAC verified, physically read-only)

Draft stacked on [R0.21 PR #161](https://github.com/Garyu-tt/mojum/pull/161).
All code is optional. No changes to main, a user's PRIVATE Noosphere,
their live creature/agent, Android/Termux, VMs, or any production key.

## Why an observer?

The canonical writer can append new experiences, but a restored
historical replica must remain in **quarantine** to avoid accidental
split-brain. Merely relying on SQLite INSERT triggers does not make
the Python reader itself physically read-only: ordinary
`SegmentedNoosphere` opens a writable connection and prepares WAL.

R0.22 implements `QuarantinedObserver`: **never** calls the writable
journal constructor, opens an existing 0600 recovered SQLite with
`mode=ro&immutable=1` and `PRAGMA query_only=ON`, and refuses
existing WAL/SHM sidecars rather than accidentally ignoring uncommitted
events. The only supported operation is a HMAC-authenticated historical
observation, not a new writer, live Aquarium, synchronization service,
or event modification. Normal `append` and `append_many` are
explicitly forbidden even before SQLite refuses writes.

## Independent trust and custody checks

The caller supplies the exact actor ID, separately protected 32-byte
HMAC key and the out-of-band independently pinned receipt SHA-256.
The viewer validates the receipt, its schema and owner HMAC, reads the
`recovery_custody` record whose actor, source count, original head,
receipt digest and state must match, verifies all append-denial and
custody-immutability triggers are still present, and streams through
the **entire source event and segment SHA-chain**. A local attacker
who replaced or truncated events cannot pass this with only an
archive-self-declared digest.

These controls authenticate a *selected historical checkpoint*, not
prove which of two divergent live writers is newest. The file-owner
or administrator can deliberately drop SQLite triggers: that is
a possible local action, not authorized writer promotion.

## Privacy of observations

Default `summary()` returns only total journal events, sealed/tail
counts, selected original head, actor ID, quarantine status and
authentication/read-only statuses. **PRIVATE topics, timestamps,
event identifiers and payloads are redacted by default.**

The operator may explicitly set `include_private_metadata=True`
to see the **64 most frequent topic buckets**, with an exact
`other_topics_event_count` for remaining event types, plus 0–64 recent
`seq/topic/recorded_at_ns/visibility` metadata records. Even then,
**no event payload, raw learning state, genome, key, memory or PRIVATE
object content** is emitted. Metrics count recorded events;
they do **not** claim to evaluate real intelligence, feelings or
learning quality. A future user-visible aquarium can use this
metadata contract without enabling historical writes.

## Offline CLI

```bash
PYTHONPATH=src python -m mojum.segment_archive_cli observe-replica \
  --db /owner-private/RECOVERED-QUARANTINED.sqlite3 \
  --receipt-file /owner-private/receipts/source.json \
  --key-file /owner-private/secrets/key-32bytes.bin \
  --expected-receipt-sha INDEPENDENTLY_PINNED_RECEIPT_SHA256 \
  --expected-agent-id dante-fixture-001

# Only when explicitly desired, expose metadata, never raw PRIVATE event data:
PYTHONPATH=src python -m mojum.segment_archive_cli observe-replica \
  --db /owner-private/RECOVERED-QUARANTINED.sqlite3 \
  --receipt-file /owner-private/receipts/source.json \
  --key-file /owner-private/secrets/key-32bytes.bin \
  --expected-receipt-sha INDEPENDENTLY_PINNED_RECEIPT_SHA256 \
  --expected-agent-id dante-fixture-001 \
  --include-private-metadata --recent 4
```

Metadata-only stdout excludes private database/key file paths.

## Synthetic acceptance

`tests/test_quarantined_observer.py` first constructs 130 PRIVATE
canonical events, exports exactly 128 sealed and 2 tail events,
signs with owner key/receipt and runs R0.21 recovery into a new
quarantined database. The viewer must:

- verify the *entire signed source chain* and all quarantine custody
  records against independently supplied expected SHA;
- expose no topic/timestamp without explicit operator opt-in;
- reject appends, UPDATE/INSERT against its SQLite file even if a
  caller accesses the raw connection, and create **no WAL/SHM files**
  or modify SQLite bytes during observation;
- reject changed signed digest/key, wrong actor, tampered custody
  triggers, a world-readable parent, symlinks and active WAL sidecar;
- exercise 130 distinct synthetic PRIVATE topic names; even then
  the response exposes no more than 64 topic buckets, with exactly
  66 events reported in the remaining-topic overflow counter;
- inspect a real **130-frame v4 Aquarium** (plus bootstrap event)
  through the owner-HMAC verified historical replica: the authorized
  summary reports precisely 130 `aquarium.frame` and one
  `aquarium.bootstrap`, whereas default output does not expose topics;
- run the offline CLI with default redaction and bounded private
  metadata, without exposing PRIVATE event payloads to stdout.

Python 3.11–3.13 CI additionally re-runs the previous signed
recovery tests. Tests use only disposable synthetic data and do not
run the creature on a real phone or remote server.

## Explicit nonclaims

Not automatic writer promotion, not a distributed consensus protocol,
not an authenticated real second device, not continuous replication,
not data encryption at rest, not a real childhood or AI evaluation.
The untrusted owner of a database can modify both local SQLite and
locally held keys/digests; trusted out-of-band root custody remains
necessary. Physical failover, operator-confirmed fencing and
multi-domain proof remain in [Issue #141](https://github.com/Garyu-tt/mojum/issues/141).
Keep Draft until exact-head CI and privacy review pass.
