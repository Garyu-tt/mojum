# Noosphere source-consent gate R0.1

## Purpose

`mojum.noosphere.source-consent/1` is an explicit, read-only admission policy for an already-produced Noosphere dataset projection. It keeps the source selection decision separate from the journal writer and from the existing PUBLIC-only dataset default.

A grant is scoped to an exact `agent_id`, `source`, `source_revision`, topic allowlist, visibility allowlist, purpose and half-open validity window `[valid_from_ns, valid_to_ns)`. A null end is unbounded. No wildcard matching is provided. Unknown fields, duplicate identifiers, malformed time values and ambiguous lists fail closed.

## API

```python
from mojum.noosphere_consent import authorize_dataset

authorized = authorize_dataset(
    dataset,                  # existing dataset() projection + sha256
    [consent_grant],          # caller-supplied explicit grants
    as_of_ns=1_800_000_000,
    purpose="offline-evaluation",
)
```

The input must already have been obtained from a chain-verified Noosphere read. The helper rechecks the projection digest, exact shape, bounds, sequence ordering, event identity, JSON-compatible values, and every declared dataset filter (`topic`, `source`, recorded-time cutoff, validity instant, and PUBLIC-only visibility mode). A projection that contains an item excluded by its own filters is rejected, even when its digest has been recomputed. It does **not** re-verify the SQLite chain; use `LocalNoosphere.verify_chain()` first and preserve its independently trusted head when that is required.

Only events with a matching active grant are returned. The output is detached, deterministic, versioned and contains a SHA-256 digest binding the source projection, grant digests and admitted event list. No source event is modified or deleted.

## Security and privacy boundaries

- No SQLite/file/network access; no journal reads or writes.
- No implicit consent: absence of a matching grant means exclusion.
- PRIVATE and RESTRICTED events require explicit inclusion in `allowed_visibility`; PUBLIC is not automatically granted.
- Consent time is evaluated using an explicit caller-supplied timestamp, never the wall clock.
- Exact source revision matching prevents a grant for one revision from silently authorizing later source content.
- Inputs and output are bounded: at most 128 grants, 64 topics per grant, 10,000 events, and 16 MiB canonical JSON.
- The gate is an application policy helper, not an identity provider, signature verifier, legal-consent system or evidence that the named grant author actually approved anything.
- The dataset's `event_hash` values are carried through but not independently authenticated here. The projection digest is not a replacement for the journal's hash-chain verification.
- Grant hashes are consistency identifiers, not signatures or encryption.

## Reproducibility

Run the focused suite with Python 3.11+:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_noosphere_consent.py' -v
```

No third-party packages are required. The implementation is opt-in and is not wired into `LocalNoosphere.dataset()`, Aquarium, Graph IR or canonical journal persistence. Independent review and exact-head CI are required before merge.
