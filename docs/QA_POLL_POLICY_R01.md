# QA polling policy R0.1

This is a pure, deterministic scheduling policy for a future read-only GitHub
poller. It is not a GitHub client or a running supervisor.

## API

- `backoff_delay()`: capped exponential delay with optional Retry-After delta
  seconds or HTTP-date. The caller supplies the current epoch for HTTP dates.
- `Circuit` / `circuit_transition()`: immutable circuit state. Transient
  failures accumulate; forbidden and permanent outcomes open the circuit.
  A successful probe resets it.
- `poll_permitted()` / `begin_probe()`: allow one half-open probe after the
  configured cool-down. A second concurrent probe is refused by the state
  contract; a real multi-worker service must persist this state transactionally.

## Safety and limitations

No network, credentials, filesystem, SQLite, timers, sleeping, random jitter,
queue state, model provider or merge API is used. The caller must serialize
circuit updates and must treat `forbidden` as a permission/configuration
incident, not retry it aggressively. Timed half-open probing is only a
recovery diagnostic; it does not imply authorization. All delays are capped
at 24 hours for Retry-After and at the caller's configured maximum for normal
backoff. This module alone does not implement rate-limit header extraction,
global multi-process coordination, GitHub polling, or deployment.
