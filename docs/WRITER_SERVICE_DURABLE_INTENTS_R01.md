# Mojum R0.9 — optional durable-intent Unix writer daemon

This PR integrates R0.8's **durably prepared exact event hash** into the
single-host Linux Unix-domain socket writer daemon from R0.4/R0.5.
It is stacked on PR #92 and changes nothing on `main` or any user's host.

## Default remains unchanged

Without `--durable-intents`, the daemon uses the same legacy local
`GuardedAquarium` code path, preserving the existing test fixtures and
local cooperative single-host behavior. RPC `status` includes the
`write_protocol` metadata field with value `legacy_local_guard_v1`.

With the explicit `--durable-intents` switch, the daemon constructs
`IntentGuardedAquarium` instead. It requires a previously installed
`write_intents` table in the **existing, trusted local authority SQLite**
and refuses to start if the table is missing, the source journal is not
fully in sync with the registry, or the source is FROZEN.

There is no fallback from durable mode to legacy mode, no auto-install
of authority schema and **no automatic recovery or unfreeze**. The status
metadata then reads `write_protocol: durable_intent_v1`.

All requests still use the existing bounded JSON RPC protocol over
private Linux `AF_UNIX`: `status`, `step`, `acquire`, and `export`.
No TCP/remote endpoint, new recovery RPC, reset RPC or administrative
authority grant is added. The service continues to support an optionally
enrolled separate client UID and a private service-owned database.

## Explicit setup on isolated nonproduction Linux

First provision a trusted synthetic Aquarium journal and local authority
registry as described in the predecessor PRs. Independently confirm a
healthy ACTIVE state and no unresolved history divergence.

As the trusted **non-root writer service UID**, explicitly install the
write-intent schema *before* starting the daemon:

```bash
PYTHONPATH=src python -m mojum.write_intent install \
  --registry /var/lib/mojum/private/authority.sqlite3
```

Then start the writer (as the same non-root service UID) with the flag:

```bash
PYTHONPATH=src python -m mojum.writer_service serve \
  --socket /run/mojum-writer/writer.sock \
  --db /var/lib/mojum/private/dante.sqlite3 \
  --registry /var/lib/mojum/private/authority.sqlite3 \
  --archive-dir /var/lib/mojum/private/exports \
  --genome examples/genesis_seed.json \
  --graph examples/first_loop.json \
  --writer node-a --epoch 1 --generation 1 \
  --client-uid 1102 --client-gid 1103 \
  --durable-intents
```

The UID/GID numbers are **examples only**. They must be replaced by the
actual authorized local accounts; see `docs/WRITER_UID_SEPARATION_R01.md`
for the private directory and group socket contract.

A client can then perform one step using the same old RPC call:

```bash
echo '{"op":"step","expected_generation":1,"dx":0.01,"dy":0}' \
  | PYTHONPATH=src python -m mojum.writer_service request \
    --socket /run/mojum-writer/writer.sock
```

## Timeline and failure policy

Each accepted frame/acquisition does the following:

1. Validate the proposed event and durable registry owner/epoch/generation.
2. **Commit a pre-append exact expected event hash** and FROZEN writer state
   in a single local-authority SQLite transaction.
3. Append the real PRIVATE Noosphere event via the existing Aquarium API.
4. Verify the committed event hash, count and tick against the prior
   durable intent.
5. Atomically finalize the registry anchor/generation, remove the intent
   and restore ACTIVE authority.

A test-only Python subprocess deliberately calls `os._exit` at each
stage; **these fault hooks exist only in the test file, never the
production daemon or its RPC**.

| Process termination | Trusted registry | Next restart |
| --- | --- | --- |
| After prepare, before journal append | FROZEN with unconsumed intent | STOP, inspect as PREPARED_NO_EVENT_STOP |
| After journal append, before registry finalize | FROZEN with matching committed hash | STOP, inspect as PREPARED_EXACT_EVENT_REQUIRES_REVIEW |
| After registry finalize, before RPC reply | ACTIVE, incremented generation, no pending intent | Restart at actual new generation only; stale clients denied |

The last case is a successful database commit whose RPC acknowledgment
was lost, **not** a license for clients to repeat the old request. The
operator must verify the persisted generation and stale socket pathname.

There is no online retry of an ambiguous operation, no trusting higher
ticks alone, no automatic cleanup of stale socket paths, and no automatic
authority transfer. `inspect_prepared` can classify the saved intent,
but every report has `auto_repair_allowed=false` and
`automatic_writer_promotion_allowed=false`.

## Regression evidence and scope

```bash
PYTHONPATH=src python -m unittest discover -s tests \
  -p 'test_writer_service_durable_intents_r01.py' -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

The real Linux 3-account acceptance workflow additionally tests an actual
`mojumqaclient` UID performing a **durably prepared** step while the
OS kernel refuses its direct SQLite file access.

**Limits:** this is single-host cooperative authority. A malicious process
with the service UID, or host root, can still modify local SQLite stores.
No signatures, remote quorum, distributed fencing, Android runtime,
encrypted archives, atomic two-DB commits or unattended recovery is
implemented. A network split between cloned registries remains unsafe.
The existing Aquarium and GENESIS are synthetic CPU fixtures, not
validated general AI cognition.

Before deployment, require a separate security review of OS identity,
WAL/file permissions, crash durability under power loss and the entire
stack of dependent PRs. Do not merge or deploy automatically.
