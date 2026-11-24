# Agent heartbeat admission R0.1

Issue: #717  
Module: `mojum.agent_heartbeat`  
Schema: `mojum.agent-heartbeat/1`

## Purpose

This opt-in, offline helper authenticates one caller-supplied external-agent
lifecycle event using HMAC-SHA256. It provides a narrow admission boundary for
`start`, `heartbeat`, and `finish` messages. It does not contact Cezar or
GitHub, inspect a process, prove liveness, persist replay state, or alter the
existing Mojum runtime.

## Envelope

The signed payload contains exactly:

- `schema`: `mojum.agent-heartbeat/1`
- `agent_id`, `run_id`: bounded ASCII identifiers
- `sequence`: nonnegative signed-64-bit integer
- `event`: `start`, `heartbeat`, or `finish`
- `issued_at_ns`, `expires_at_ns`: nonnegative signed-64-bit timestamps

The envelope adds a lowercase 64-character hexadecimal `signature`. The
signature is HMAC-SHA256 over UTF-8 canonical JSON of the payload fields
(excluding signature), using sorted keys, compact separators, no NaN/Infinity,
and UTF-8 text. JSON wire input must itself use this canonical representation
and may not contain duplicate keys.

## Example

```python
from mojum.agent_heartbeat import sign_heartbeat, verify_heartbeat

key = b"replace-with-a-secret-from-a-secret-manager-32bytes"
now_ns = 1_000_000_000
message = sign_heartbeat(
    agent_id="worker-01",
    run_id="run-2026-11-24-a",
    sequence=1,
    event="start",
    issued_at_ns=now_ns,
    expires_at_ns=now_ns + 10_000_000_000,
    key=key,
)
receipt = verify_heartbeat(message, key=key, now_ns=now_ns)
```

Production keys must be generated and stored by the deployment's secret
manager, scoped per agent, rotated, and never included in logs or source.

## Admission rules and limitations

- Keys must be exact `bytes` values of 32..128 bytes.
- Envelopes are bounded to 16 KiB; IDs and integer domains are bounded.
- Default validity is at most 60 seconds with at most 5 seconds of future
  clock skew. Both limits can be narrowed or widened within hard ceilings.
- Expired, premature, malformed, noncanonical, altered, and unauthenticated
  events fail closed.
- The returned immutable receipt contains identifiers, sequence, event,
  timestamps, and a payload digest only. It never includes the key/signature.
- The caller must atomically persist and compare the high-water sequence for
  each `(agent_id, run_id)` to prevent replay and out-of-order acceptance.
  This module is deliberately stateless; two callers with stale high-water
  snapshots can both accept the same message.
- A valid signature proves possession of the shared key, not that the named
  agent process is currently alive, healthy, authorized for a particular
  capability, or connected to GitHub. Authorization, lease ownership, process
  health, and GitHub PR lifecycle remain separate checks.

## Verification

Run the focused stdlib suite:

```sh
PYTHONPATH=src python -m unittest tests.test_agent_heartbeat -v
```

No third-party runtime dependencies, network, filesystem access, subprocess,
private journal access, or live process control are used by the module.
