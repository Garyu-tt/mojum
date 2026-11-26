# NATS JetStream delivery contract R0.1

## Purpose and boundary

This is an offline, standard-library-only reference contract for a future opt-in NATS JetStream adapter. It is not a NATS client, broker, scheduler, durable journal, authentication system, consensus protocol, or proof of exactly-once delivery. No SDK is imported and no network operation is performed.

The envelope contains **metadata only**. Payload bytes are not accepted. The SHA-256 field is a caller-supplied content commitment, not authentication. Only explicitly classified `PUBLIC_SYNTHETIC` fixtures are admitted. PRIVATE Noosphere records, prompts, credentials, hostnames, IP addresses, and hardware/control data are out of scope.

## Version 1 envelope

Exact keys: `schema`, `provenance`, `node_id`, `stream`, `subject`, `event_id`, `delivery_id`, `sequence`, `tick`, `payload_sha256`, `causation_id`.

- Schema is `mojum.nats-delivery/1`; provenance is `PUBLIC_SYNTHETIC`.
- Identifiers are bounded ASCII tokens (1-96 characters).
- Sequence and logical tick are exact native integers in [0, 2^63-1]; booleans are rejected.
- Payload digest is exactly 64 lowercase hexadecimal characters.
- Causation ID is null or a bounded token.
- Unknown, missing, duplicate-by-construction, malformed, or oversized fields fail closed.

## Delivery planning

`plan_delivery` is a pure advisory function. It returns `DUPLICATE` for a previously observed scoped delivery key, `STALE` for a sequence not advancing the supplied cursor, `GAP` when reconciliation is required, or `ACCEPT_CANDIDATE` when metadata is admissible and contiguous. An ACCEPT_CANDIDATE is not an acknowledgement or durable commit. A real adapter must persist its consumer cursor and dedup state atomically with the downstream effect before acknowledging the broker message.

A future adapter must map the coordinator-owned lease UUID and attempt to an idempotent execution identity; propagate cancellation; bound in-flight messages and memory; implement explicit ack deadlines, redelivery limits, backpressure and reconnect behavior; and reconcile ambiguous publish/ack outcomes before retrying. JetStream delivery is at-least-once unless the complete application protocol proves stronger semantics. This contract makes no exactly-once claim.

## Required integration gates

1. Pin NATS server and client versions; review licenses and platform support.
2. Keep the current SQLite coordinator as the sole task/lease/fencing authority.
3. Use capability-scoped credentials and TLS; never place secrets in envelopes or logs.
4. Test duplicate delivery, lost acknowledgement, process restart, sequence gaps, consumer recreation, partition, slow consumer, broker restart, cancellation and disk pressure.
5. Measure CPU, resident memory, queue depth, redelivery rate and end-to-end latency against the existing SQLite path before adding a service dependency.
6. Require exact-head CI and independent QA. No production deployment or automatic merge is authorized by this document.

## Test

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_nats_delivery_contract.py' -v
```
