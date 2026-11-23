"""Offline HMAC-backed node attestation for the reference Continuity Mesh.

This is a shared-secret reference profile, not public-key signatures, transport
security, durable enrollment, or a revocation service.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
import re

from .mesh_contract import Offer, PROTOCOL, _validated

SCHEMA = "mojum.mesh-attestation/1"
MAX_TTL_SECONDS = 30 * 24 * 60 * 60
CLOCK_SKEW_SECONDS = 300
_MIN_KEY_BYTES = 32
_MAX_KEY_BYTES = 128
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)
_STATEMENT_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)
_HEX_MAC = re.compile(r"[0-9a-f]{64}", re.ASCII)


class AttestationError(ValueError):
    """Malformed or invalid node-attestation input."""


class AttestationDenied(PermissionError):
    """A valid-looking attestation is not trusted or is outside its validity."""


@dataclass(frozen=True)
class NodeAttestation:
    schema: str
    key_id: str
    node_id: str
    protocol: str
    capabilities: tuple[str, ...]
    issued_at: int
    expires_at: int
    statement_id: str
    signature: str


def _require_text(value, pattern, what):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise AttestationError("invalid " + what)


def _require_epoch(value, what):
    if type(value) is not int or not (0 <= value <= 2**63 - 1):
        raise AttestationError("invalid " + what)


def _validate_key(key, *, what="authority key"):
    if type(key) is not bytes or not (_MIN_KEY_BYTES <= len(key) <= _MAX_KEY_BYTES):
        raise AttestationError(what + " must be 32..128 raw bytes")
    return key


def _payload(attestation):
    """Validate and serialize signed fields; signature is deliberately excluded."""
    if type(attestation) is not NodeAttestation:
        raise AttestationError("expected exact NodeAttestation")
    if attestation.schema != SCHEMA:
        raise AttestationError("unsupported attestation schema")
    _require_text(attestation.key_id, _KEY_ID, "key id")
    _require_text(attestation.statement_id, _STATEMENT_ID, "statement id")
    _require_epoch(attestation.issued_at, "issued_at")
    _require_epoch(attestation.expires_at, "expires_at")
    if attestation.expires_at <= attestation.issued_at:
        raise AttestationError("expiry must follow issue time")
    if attestation.expires_at - attestation.issued_at > MAX_TTL_SECONDS:
        raise AttestationError("attestation lifetime exceeds maximum")
    if type(attestation.capabilities) is not tuple or len(attestation.capabilities) > 128:
        raise AttestationError("invalid capabilities")
    try:
        _validated(Offer(attestation.protocol, attestation.node_id,
                         attestation.capabilities))
    except (TypeError, ValueError) as exc:
        raise AttestationError("invalid attested mesh identity or capabilities") from exc
    if tuple(sorted(attestation.capabilities)) != attestation.capabilities:
        raise AttestationError("capabilities must be sorted canonically")
    _require_text(attestation.signature, _HEX_MAC, "signature")
    data = {
        "schema": attestation.schema,
        "key_id": attestation.key_id,
        "node_id": attestation.node_id,
        "protocol": attestation.protocol,
        "capabilities": list(attestation.capabilities),
        "issued_at": attestation.issued_at,
        "expires_at": attestation.expires_at,
        "statement_id": attestation.statement_id,
    }
    return json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _mac(payload, key):
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def sign_attestation(*, key_id, authority_key, node_id, capabilities,
                     issued_at, expires_at, statement_id):
    """Create an authority MAC statement; keep authority_key secret and local."""
    _validate_key(authority_key)
    if type(capabilities) not in (tuple, list):
        raise AttestationError("capabilities must be a tuple or list")
    if any(type(x) is not str for x in capabilities):
        raise AttestationError("capabilities must contain plain strings")
    caps = tuple(sorted(capabilities))
    draft = NodeAttestation(
        SCHEMA, key_id, node_id, PROTOCOL, caps, issued_at, expires_at,
        statement_id, "0" * 64,
    )
    payload = _payload(draft)
    return NodeAttestation(
        draft.schema, draft.key_id, draft.node_id, draft.protocol,
        draft.capabilities, draft.issued_at, draft.expires_at,
        draft.statement_id, _mac(payload, authority_key),
    )


def verify_attestation(attestation, authority_keys, *, now):
    """Verify authority MAC and validity window; return the verified statement."""
    payload = _payload(attestation)
    _require_epoch(now, "verification time")
    if attestation.issued_at > now + CLOCK_SKEW_SECONDS:
        raise AttestationDenied("attestation is not yet valid")
    if attestation.expires_at <= now:
        raise AttestationDenied("attestation has expired")
    if type(authority_keys) is not dict:
        raise AttestationError("authority_keys must be an exact dict")
    key = authority_keys.get(attestation.key_id)
    if key is None:
        raise AttestationDenied("unknown authority key")
    _validate_key(key)
    expected = _mac(payload, key)
    if not hmac.compare_digest(expected, attestation.signature):
        raise AttestationDenied("invalid authority signature")
    return attestation


def verify_offer(offer, attestation, authority_keys, *, now):
    """Authorize exactly the node/capabilities covered by a verified statement."""
    if type(offer) is not Offer:
        raise AttestationError("expected exact mesh Offer")
    try:
        _validated(offer)
    except (TypeError, ValueError) as exc:
        raise AttestationError("invalid capability offer") from exc
    verified = verify_attestation(attestation, authority_keys, now=now)
    if offer.node_id != verified.node_id or offer.protocol != verified.protocol:
        raise AttestationDenied("offer identity does not match attested node")
    if frozenset(offer.capabilities) != frozenset(verified.capabilities):
        raise AttestationDenied("offer capabilities exceed or differ from attestation")
    return verified
