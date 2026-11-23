# Noosphere authenticated export envelope R0.1

## Purpose

The schema is **mojum.noosphere.encrypted-envelope/1**. It is an opt-in byte-level wrapper for export or snapshot payloads, providing confidentiality and integrity for one caller-supplied byte string using AES-256-GCM. It is outside the canonical SQLite event journal and does not change its persisted schema.

## API

Install the optional provider only in the environment that explicitly uses this feature:

~~~sh
python -m pip install 'cryptography>=42'
~~~

~~~python
from mojum.noosphere_envelope import seal_bytes, open_bytes

context = {
    "key_id": "backup-key-2026-01",  # identifier only; key stays with caller
    "agent_id": "dante-local",
    "visibility": "PRIVATE",
    "content_type": "application/vnd.mojum.snapshot+json",
}
envelope = seal_bytes(snapshot_bytes, key=key_32_bytes, **context)
restored = open_bytes(
    envelope,
    key=key_32_bytes,
    expected_key_id=context["key_id"],
    expected_agent_id=context["agent_id"],
    expected_visibility=context["visibility"],
    expected_content_type=context["content_type"],
)
~~~

The provider is imported lazily. Importing Mojum core or this module does not require cryptography. Keys must be exactly 32 bytes. Each encryption obtains a fresh 12-byte nonce from Python's secure random source. AES-GCM authenticates a canonical associated-data tuple containing schema, algorithm, key ID, agent ID, visibility and content type. Decryption requires the caller to repeat the expected context; mismatch is rejected before decryption.

## Wire contract and limits

- Canonical UTF-8 JSON envelope, exact fields only, duplicate keys rejected.
- AES-256-GCM, 96-bit nonce and 128-bit authentication tag.
- Plaintext limit: 1 MiB. Envelope limit: `4 * ceil((plaintext_bytes + 16-byte GCM tag) / 3) + 8 KiB`, accounting for Base64 ciphertext expansion and bounded metadata.
- Supported visibility strings: PUBLIC, PRIVATE, RESTRICTED.
- No compression, file access, network access, database writes or implicit key lookup.
- Authentication failure, malformed encoding, wrong key or context raise NoosphereEnvelopeError; unauthenticated bytes are never returned.

## Security boundary

This is an authenticated-encryption primitive, not a complete encrypted-memory system.

- The caller must provision, protect, rotate and revoke keys. key_id is metadata, not a key-management mechanism.
- Nonce uniqueness is required per key. Nonces are generated internally; callers must not patch or replace the secure random source in production.
- The envelope does not encrypt current SQLite journal rows, authenticate the source snapshot, prove consent, erase old plaintext copies, provide rollback protection, or establish device identity.
- No claim is made about key zeroization in Python memory, filesystem permissions, hostile-process isolation, traffic security or distributed recovery.
- Agent and visibility values are authenticated labels supplied by the caller; this helper does not authorize their correctness.
- Cryptography and its native backend are optional runtime dependencies and must be assessed for target platform and license by the consuming application.

## Tests

~~~sh
PYTHONPATH=src python -m unittest discover -s tests -p 'test_noosphere_envelope.py' -v
~~~

Tests use synthetic fixture bytes and a fixed test key. The nonce is patched only in one repeatability test. A green local test or CI run does not make existing Noosphere storage encrypted and does not authorize production key handling.
