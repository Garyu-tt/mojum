# Noosphere checkpoint reference manifest R0.1

This proposal adds a **metadata-only** versioned contract for grouping one
checkpoint's five component references: brain, genome, body, world and memory.
It is opt-in and independent of the existing Noosphere SQLite journal API.

## Contract

Schema: `mojum.noosphere.checkpoint/1`.

Every component reference contains exactly:

- `schema`: component snapshot schema identifier
- `revision`: source component revision identifier
- `content_sha256`: lowercase SHA-256 of the component snapshot bytes
- `visibility`: PUBLIC, PRIVATE or RESTRICTED
- `agent_id`: owning organism identifier
- `tick`: causal simulation tick

A manifest contains exactly five uniquely named component references, all
sharing the same organism and causal tick. The canonical manifest digest binds
the component map, checkpoint identity, tick and optional previous checkpoint
digest. Canonical JSON is bounded to 16 KiB; duplicate keys, non-UTF-8 strings,
noncanonical encodings, non-native dictionary keys, missing/extra references and digest mismatches fail
closed.

## Use

```python
from mojum.noosphere_checkpoint import (
    create_checkpoint, verify_checkpoint, encode_checkpoint, decode_checkpoint,
)

manifest = create_checkpoint(
    "dante",
    "checkpoint-0042",
    42,
    {
        # Supply exact six-field references for brain, genome, body, world,
        # and memory. Each reference must use the same agent_id and tick.
    },
    previous_checkpoint_sha256=None,  # genesis; otherwise prior manifest digest
)
wire = encode_checkpoint(manifest)
verified = decode_checkpoint(wire)
verify_checkpoint(
    verified,
    expected_previous_checkpoint_sha256=None,  # CAS expectation for genesis
)
```

For a successor, supply its previous manifest digest when creating the
checkpoint and pass the independently observed current head as
`expected_previous_checkpoint_sha256` to verification. A mismatch is a
stale-CAS refusal. This helper does not retain or atomically advance that head:
the storage layer must implement the actual transaction and concurrency gate.

## Safety and explicit limits

- The manifest contains references and hashes, **not component payloads**.
- A SHA-256 digest detects content mismatch only when the expected digest is
  trusted independently. It is not a signature, authentication, consent proof,
  authorization decision or protection against an attacker replacing both
  payload and manifest.
- Visibility values are carried as metadata; this helper does not grant access
  or make PRIVATE/RESTRICTED content exportable.
- No SQLite writes, journal migration, encryption, key management, snapshot
  capture, restore, replication, network, device control or runtime integration.
- Component schema/revision identifiers are opaque bounded labels. Their own
  loaders must validate the referred payload bytes and schema semantics.
- The pure-Python API is stdlib-only and intentionally has no mandatory
  dependency. It does not claim crash atomicity; durable publication belongs
  to a future transaction-aware storage adapter.

Run the focused hermetic tests:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_noosphere_checkpoint.py' -v
```

Exact-head GitHub Actions and independent review are required before merge.
