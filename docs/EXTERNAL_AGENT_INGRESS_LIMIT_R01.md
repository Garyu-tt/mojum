# External-agent ingress admission limit R0.1

This is a small, opt-in, process-local admission policy for a caller that has
**already authenticated** an external-agent receipt source. It does not expose
an HTTP endpoint, validate HMAC receipts, provision keys, or establish worker
liveness. Authentication must happen before calling `IngressRateLimiter.admit`.

## Contract

- Fixed windows are keyed by a bounded source identifier (1..128 ASCII chars).
- Caller supplies a monotonic nanosecond timestamp; booleans, floats, negative
  values, oversized values and clock rollback fail closed.
- Defaults: 30 attempts per 60 seconds and at most 1,024 simultaneously tracked
  sources. Constructor limits are strict native integers with hard upper bounds.
- At the per-source limit, the result includes a deterministic
  `retry_after_ns`. A full source table rejects new identities rather than
  evicting an active source and allowing a quota bypass.
- Expired windows are pruned on admission. State is process-local and protected
  by a lock; it is not durable or shared across workers/hosts.

## Example

```python
from time import monotonic_ns
from mojum.external_agent_ingress_limit import IngressRateLimiter

limiter = IngressRateLimiter(limit=30, window_ns=60_000_000_000)
decision = limiter.admit(authenticated_source_id, monotonic_ns())
if not decision.allowed:
    # Map to a transport-specific 429 only in the owning HTTP adapter.
    raise RuntimeError(decision.reason)
```

## Validation

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_external_agent_ingress_limit.py' -v
```

The tests exercise quota boundaries, reset boundaries, source isolation,
capacity exhaustion, clock rollback, strict input types and concurrent calls.

## Limitations

A process-local limiter is not a distributed quota: multiple workers each have
their own allowance. It does not defend against an authenticated attacker
rotating identities, and it does not replace upstream network/connection limits.
Production ingress must add shared durable/distributed enforcement, key/source
enrollment, request-body limits, transport authentication and operational
monitoring. This helper never reads credentials, files, environment variables,
network state, PRIVATE Noosphere records or host identifiers.
