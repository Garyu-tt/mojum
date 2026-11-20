"""Portable in-memory checkpoint envelope; not an atomic durable snapshot."""
import base64
import binascii
import json

from .checkpoint_manifest import make_manifest, verify_manifest

SCHEMA = "mojum.checkpoint-bundle/1"
NAMES = ("genome", "neural", "body", "world", "memory")
MAX_COMPONENT_BYTES = 4_000_000


def _decode(data):
    if type(data) is not str:
        raise ValueError("component encoding must be text")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 component") from exc
    if len(raw) > MAX_COMPONENT_BYTES or base64.b64encode(raw).decode("ascii") != data:
        raise ValueError("noncanonical or oversized component")
    return raw


def pack(organism_id, revision, components):
    """Bind five byte payloads to one deterministic, self-checking JSON envelope."""
    if type(components) is not dict or set(components) != set(NAMES):
        raise ValueError("exactly five components required")
    for value in components.values():
        if type(value) is not bytes or len(value) > MAX_COMPONENT_BYTES:
            raise ValueError("component must be bounded bytes")
    manifest = make_manifest(organism_id, revision, components)
    return {"schema": SCHEMA, "manifest": manifest,
            "payloads": {name: base64.b64encode(components[name]).decode("ascii") for name in NAMES}}


def unpack(bundle, *, expected_revision=None, expected_organism_id=None):
    """Validate everything before exposing any component bytes to a caller."""
    if type(bundle) is not dict or set(bundle) != {"schema", "manifest", "payloads"}:
        raise ValueError("invalid bundle shape")
    if bundle["schema"] != SCHEMA or type(bundle["payloads"]) is not dict or set(bundle["payloads"]) != set(NAMES):
        raise ValueError("unsupported schema or components")
    payloads = {name: _decode(bundle["payloads"][name]) for name in NAMES}
    verify_manifest(bundle["manifest"], payloads, expected_revision=expected_revision)
    # Each organism has its own checkpoint namespace. A valid digest and
    # revision alone cannot prevent restoring another organism's payload.
    if expected_organism_id is not None and (
            type(expected_organism_id) is not str
            or bundle["manifest"]["organism_id"] != expected_organism_id):
        raise ValueError("checkpoint organism identity mismatch")
    return payloads


def dumps(bundle):
    """Canonical transport JSON; never implies disk durability or authentication."""
    unpack(bundle)
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def loads(wire, *, expected_revision=None, expected_organism_id=None):
    if type(wire) is not str or len(wire) > 30_000_000:
        raise ValueError("invalid bundle wire")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        bundle = json.loads(wire, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid bundle JSON") from exc
    unpack(bundle, expected_revision=expected_revision,
           expected_organism_id=expected_organism_id)
    if dumps(bundle) != wire:
        raise ValueError("noncanonical bundle JSON")
    return bundle
