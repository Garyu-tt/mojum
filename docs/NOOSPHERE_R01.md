# Noosphere R0.1 — local-first episodic memory for Mojum

This module is a **genuinely persistent** CPU-only event store for **one digital agent on one host**. It is NOT a multi-host Noosphere 3.0, not an ML memory, and not integrated yet with the pending GENESIS/GraphForge/room PRs. See [issue #8](https://github.com/Garyu-tt/mojum/issues/8).

## Contract

- Store: `mojum.noosphere.sqlite/1` / SQLite WAL, `synchronous=FULL`, one explicit database file per agent; no external services or network.
- Event: `mojum.noosphere.event/1`: `event_id`, `agent_id`, `topic`, `observed_at_ns`, `recorded_at_ns`, `valid_from_ns`, optional `valid_to_ns`, `visibility`, `source`, `source_revision`, JSON-object `payload`.
- All event fields validated strictly and bounded to <=128 KiB of serialized event data; no binary attachments, dynamic Python code or automatic plugin execution.
- Append via `BEGIN IMMEDIATE` + `expected_head` compare-and-swap. Concurrent writers do not silently overwrite. Duplicate IDs and nonmonotonic record times rejected.
- SQLite `BEFORE UPDATE/DELETE` triggers protect append-only access through normal SQL statements; complete transactions are atomic. `verify_chain()` checks full event representation against indexed columns, contiguous sequence and cumulative SHA-256 event chain.
- `dataset()` is read-only, ordered, filtered and deterministic for a fixed database state and parameter set; exports `PUBLIC` only unless `include_nonpublic=True` is explicitly supplied by the trusted same-owner process. Supports topic, source, as-of recorded time, valid-at time and row limit.

## Security boundary — IMPORTANT

**This is not authenticated, encrypted or tamper-proof storage.** Local filesystem protection and the calling process are the sole access boundary. Any process that can access the DB file may read private events or drop SQL triggers. SHA-256 chaining detects incidental corruption and can only detect adversarial rewriting with a separately trusted external head anchor. SQLite WAL may create `-wal`/`-shm` neighbors that require equivalent filesystem permissions. Never store private or sensitive real-world data here until authentication, encryption, secret-safe key management and tenant isolation have been independently reviewed.

**Not yet delivered:** >=2 independent replicas, full file encryption, cross-host replication/failover, Noosphere adaptive datasets, neural training, real-time sensor integration, snapshots across GENESIS/physics/brain/world, and consent/retention policy. Changes are proposed in a PR only, not deployed.

## Verify offline

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_noosphere.py' -v
PYTHONPATH=src python examples/noosphere_demo.py
```

## Planned integration

`Universal Graph IR (revisioned event port)` → agent sensor and heartbeat/physics frames → Noosphere append with original `source_revision` and a monotonic `recorded_at_ns` → read-only provenance-aware dataset projection → explicitly permitted learner. Atomically coordinate memory head and simulation tick with an independent multi-component checkpoint protocol. Node.js/Adamantium-node should be optional.

The demo records entirely synthetic events; no real neural cognition, anatomy or consciousness is implied.
