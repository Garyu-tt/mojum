"""Optional authenticated encryption for detached Noosphere export bytes.

The core package remains stdlib-only. The crypto provider is loaded lazily.
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets

SCHEMA = "mojum.noosphere.encrypted-envelope/1"
ALGORITHM = "AES-256-GCM"
_ALLOWED_VISIBILITY = frozenset({"PUBLIC", "PRIVATE", "RESTRICTED"})
_MAX_PLAINTEXT_BYTES = 1_048_576
_KEY_BYTES = 32
_NONCE_BYTES = 12
_TAG_BYTES = 16
# JSON base64-encodes ciphertext, adding 4/3 expansion. Reserve the exact
# worst-case encoded ciphertext size plus bounded metadata.
_MAX_ENVELOPE_BYTES = 4 * ((_MAX_PLAINTEXT_BYTES + _TAG_BYTES + 2) // 3) + 8192
_FIELDS = frozenset({
    "schema", "algorithm", "key_id", "agent_id", "visibility",
    "content_type", "nonce", "ciphertext",
})


class NoosphereEnvelopeError(ValueError):
    """Invalid envelope, context, key, or unavailable optional crypto provider."""


def _text(value: object, field: str, maximum: int) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > maximum:
        raise NoosphereEnvelopeError(f"{field} must be a nonempty string of at most {maximum} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NoosphereEnvelopeError(f"{field} must be valid UTF-8") from exc
    return value


def _key(value: object) -> bytes:
    if type(value) is not bytes or len(value) != _KEY_BYTES:
        raise NoosphereEnvelopeError("key must be exactly 32 bytes (AES-256)")
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise NoosphereEnvelopeError("metadata is not canonical UTF-8 JSON") from exc


def _aad(*, key_id: str, agent_id: str, visibility: str, content_type: str) -> bytes:
    return _canonical({
        "schema": SCHEMA,
        "algorithm": ALGORITHM,
        "key_id": key_id,
        "agent_id": agent_id,
        "visibility": visibility,
        "content_type": content_type,
    })


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise NoosphereEnvelopeError(
            "optional dependency missing; install cryptography to use encrypted envelopes"
        ) from exc
    return AESGCM(key)


def seal_bytes(
    plaintext: bytes,
    *,
    key: bytes,
    key_id: str,
    agent_id: str,
    visibility: str,
    content_type: str,
) -> bytes:
    """Encrypt bounded bytes into a canonical, versioned AES-GCM envelope.

    A 96-bit nonce is generated internally for every call. Keys are supplied
    by the caller and are never serialized or retained by this module.
    """
    if type(plaintext) is not bytes or len(plaintext) > _MAX_PLAINTEXT_BYTES:
        raise NoosphereEnvelopeError("plaintext must be bytes within the 1 MiB budget")
    checked_key = _key(key)
    checked_key_id = _text(key_id, "key_id", 128)
    checked_agent = _text(agent_id, "agent_id", 256)
    if type(visibility) is not str or visibility not in _ALLOWED_VISIBILITY:
        raise NoosphereEnvelopeError("visibility must be PUBLIC, PRIVATE or RESTRICTED")
    checked_type = _text(content_type, "content_type", 128)

    nonce = secrets.token_bytes(_NONCE_BYTES)
    if type(nonce) is not bytes or len(nonce) != _NONCE_BYTES:
        raise NoosphereEnvelopeError("secure nonce source returned an invalid nonce")
    aad = _aad(
        key_id=checked_key_id, agent_id=checked_agent,
        visibility=visibility, content_type=checked_type,
    )
    ciphertext = _aesgcm(checked_key).encrypt(nonce, plaintext, aad)
    envelope = {
        "schema": SCHEMA,
        "algorithm": ALGORITHM,
        "key_id": checked_key_id,
        "agent_id": checked_agent,
        "visibility": visibility,
        "content_type": checked_type,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    encoded = _canonical(envelope)
    if len(encoded) > _MAX_ENVELOPE_BYTES:
        raise NoosphereEnvelopeError("encrypted envelope exceeds maximum size")
    return encoded


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise NoosphereEnvelopeError("duplicate JSON key")
        result[name] = value
    return result


def _reject_constant(_value):
    raise NoosphereEnvelopeError("non-finite JSON values are forbidden")


def open_bytes(
    envelope_bytes: bytes,
    *,
    key: bytes,
    expected_key_id: str,
    expected_agent_id: str,
    expected_visibility: str,
    expected_content_type: str,
) -> bytes:
    """Authenticate and decrypt only in the caller's explicitly expected context."""
    if type(envelope_bytes) is not bytes or not 1 <= len(envelope_bytes) <= _MAX_ENVELOPE_BYTES:
        raise NoosphereEnvelopeError("envelope must be nonempty bytes within the size budget")
    checked_key = _key(key)
    key_id = _text(expected_key_id, "expected_key_id", 128)
    agent_id = _text(expected_agent_id, "expected_agent_id", 256)
    content_type = _text(expected_content_type, "expected_content_type", 128)
    if type(expected_visibility) is not str or expected_visibility not in _ALLOWED_VISIBILITY:
        raise NoosphereEnvelopeError("expected_visibility must be PUBLIC, PRIVATE or RESTRICTED")

    try:
        raw = envelope_bytes.decode("utf-8")
        envelope = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except NoosphereEnvelopeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise NoosphereEnvelopeError("invalid encrypted envelope JSON") from exc
    if type(envelope) is not dict or set(envelope) != _FIELDS:
        raise NoosphereEnvelopeError("encrypted envelope fields do not match schema")
    if _canonical(envelope) != envelope_bytes:
        raise NoosphereEnvelopeError("encrypted envelope is not canonical JSON")
    if (envelope["schema"] != SCHEMA or envelope["algorithm"] != ALGORITHM
            or envelope["key_id"] != key_id or envelope["agent_id"] != agent_id
            or envelope["visibility"] != expected_visibility
            or envelope["content_type"] != content_type):
        raise NoosphereEnvelopeError("encrypted envelope context mismatch")

    try:
        nonce_text = envelope["nonce"]
        ciphertext_text = envelope["ciphertext"]
        if type(nonce_text) is not str or type(ciphertext_text) is not str:
            raise ValueError("encoded fields must be strings")
        nonce = base64.b64decode(nonce_text, validate=True)
        ciphertext = base64.b64decode(ciphertext_text, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise NoosphereEnvelopeError("invalid base64 in encrypted envelope") from exc
    if len(nonce) != _NONCE_BYTES or len(ciphertext) < _TAG_BYTES:
        raise NoosphereEnvelopeError("invalid nonce or ciphertext length")

    if base64.b64encode(nonce).decode("ascii") != nonce_text or base64.b64encode(ciphertext).decode("ascii") != ciphertext_text:
        raise NoosphereEnvelopeError("noncanonical base64 in encrypted envelope")

    aad = _aad(
        key_id=key_id, agent_id=agent_id,
        visibility=expected_visibility, content_type=content_type,
    )
    # Keep the optional-dependency diagnostic distinct from authentication errors.
    cipher = _aesgcm(checked_key)
    try:
        plaintext = cipher.decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise NoosphereEnvelopeError("envelope authentication failed") from exc
    if len(plaintext) > _MAX_PLAINTEXT_BYTES:
        raise NoosphereEnvelopeError("decrypted plaintext exceeds maximum size")
    return plaintext


__all__ = ["NoosphereEnvelopeError", "seal_bytes", "open_bytes"]
