# External-agent authenticated receipts, experimental #717 slice

`mojum.external_agent_heartbeat` is a **stdlib-only**, **opt-in**, offline SQLite receipt journal. It does not deploy Cezar or Telegram endpoints, prove ChatGPT automation execution, or produce a live fleet count without signed observed events. Use it as a narrowly scoped future Cezar ingestion boundary, **not** as an alternative task scheduler or independent PR acceptance authority.

## Trust and event wire

The producer signs the canonical JSON dictionary using `HMAC-SHA256` with an out-of-band, randomly generated **32+ byte key** enrolled specifically for `(source, agent_id, repo)`. Ingest uses constant-time signature comparison. Never hard-code production keys, store them in the journal or put them in traces. Key rotation, transport authentication, deployment credentials, revocation and trusted principal provisioning remain **unimplemented**. The `source`, host, provider, repo, GitHub issue/PR/SHA and diagnostic code are authenticated **claims from the enrolled producer**, not an independent verification that a worker ran or that a PR was merged. External adapters must perform that independent work.

Required **exact** fields:

```json
{
  "schema": "mojum.external-agent-receipt/1",
  "source": "chatgpt",
  "agent_id": "peer-1",
  "host": "cpu-node-1",
  "run_id": "run-123",
  "session_id": "session-1",
  "repo": "Garyu-tt/mojum",
  "provider": "cpu-local",
  "kind": "agent.started",
  "seq": 1,
  "timestamp_ms": 1795438800000,
  "ttl_ms": 30000,
  "issue": 717,
  "pr": null,
  "sha": null,
  "status_code": "none"
}
```

Valid kinds: `agent.started`, `agent.heartbeat`, `agent.finished`, `agent.failed`. Nonterminal events require `status_code=none`, finish requires `completed`, and fail requires one of `failed`, `cancelled`, `rate_limited`, `unreachable`. No arbitrary diagnostic strings or free-form headers are accepted. Restrict DB filesystem directory to the owner; the module refuses symlinks and group/world-readable DB paths, uses SQLite FULL synchronous writes and persists no signing keys or HMAC signatures. This is not a network ingress service, and should not be used in an adversarial shared writable directory (there is no cross-process inode lease or complete defense against filesystem races).

## Minimal use

```python
from pathlib import Path
from mojum.external_agent_heartbeat import ReceiptJournal, sign_receipt

# `event` is a strict dictionary as shown above, with current timestamp_ms.
secret = load_secret_from_private_auth_store()  # caller responsibility
sig = sign_receipt(event, secret)  # producer side
with ReceiptJournal(Path("/private/receipts.sqlite3"),
                    {("chatgpt", "peer-1", "Garyu-tt/mojum"): secret}) as journal:
    accepted = journal.ingest(event, sig)
    state = journal.status("chatgpt", "peer-1", event["run_id"])
    dashboard = journal.readback(limit=1000)
```

`ingest` returns `True` for a committed new receipt, `False` for an exact authenticated duplicate. The first event must be `started/seq=1`. Thereafter sequence numbers are consecutive and run identity immutable. Conflicting same-sequence payloads, gaps, out-of-order times, post-terminal events, unknown principals/repositories, invalid signatures, and non-versioned input fail closed. New receipts must be within five minutes of **server-observed** UTC milliseconds. A duplicate cannot extend liveness. Durable readback computes `active` or `stale` from **server-observed** receipt time + signed bounded TTL (1–300 s), or retains terminal `finished`/`failed`; no receipt is `unknown`. Scheduled jobs, GitHub PRs and Telegram notifications **alone** must not be counted as live agents. The `readback` output returns `truncated=true` on overflow rather than pretending partial results are the entire fleet.

## Acceptance and remaining integration

Run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_external_agent_heartbeat.py' -v`. Tests use disposable private SQLite fixtures and synthetic clocks; exercise forged keys, duplicate replay, conflict and gap rejection, restart persistence, TTL edge, terminal states, unknown schedule-only state, source isolation, redaction, and private-file checks.

Not in this PR: Cezar ingress HTTP/auth and rate limiting, actual automation/Legion/worker adapters, authenticated external liveness measurements, native leases, UI/Telegram wiring, provider budget tracking, retention controls, key distribution/rotation, cross-node consensus, or independent GitHub lifecycle reconciliation. Live integration must be gated by separate end-to-end and independent QA against #717, #714 and #688; **do not auto-merge this receipt library**.
