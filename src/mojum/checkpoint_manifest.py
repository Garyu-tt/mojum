"""Pure-data integrity barrier for an offline multi-component checkpoint.

NOT durable storage, replication, encryption or an atomic disk commit.
"""
import hashlib
import json
import re

SCHEMA = "mojum.checkpoint-manifest/1"
_COMPONENTS = ("genome", "neural", "body", "world", "memory")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def digest_bytes(payload):
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


def make_manifest(organism_id, revision, components):
    """Commit only a complete, validated set of already encoded component bytes."""
    if not isinstance(organism_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", organism_id):
        raise ValueError("invalid organism_id")
    if type(revision) is not int or revision < 0:
        raise ValueError("invalid revision")
    if not isinstance(components, dict) or set(components) != set(_COMPONENTS):
        raise ValueError("exactly five component payloads required")
    refs = {name: digest_bytes(components[name]) for name in _COMPONENTS}
    record = {"schema": SCHEMA, "organism_id": organism_id, "revision": revision, "components": refs}
    return {**record, "manifest_sha256": hashlib.sha256(_canonical(record)).hexdigest()}


def verify_manifest(manifest, components, *, expected_revision=None):
    """Reject missing, changed, stale or malformed references before restore."""
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "organism_id", "revision", "components", "manifest_sha256"}:
        raise ValueError("invalid manifest shape")
    if manifest["schema"] != SCHEMA:
        raise ValueError("unsupported schema")
    rebuilt = make_manifest(manifest["organism_id"], manifest["revision"], components)
    if expected_revision is not None and (type(expected_revision) is not int or manifest["revision"] != expected_revision):
        raise ValueError("stale checkpoint revision")
    if not isinstance(manifest["components"], dict) or set(manifest["components"]) != set(_COMPONENTS):
        raise ValueError("invalid references")
    for key in _COMPONENTS:
        if not isinstance(manifest["components"][key], str) or not _SHA.fullmatch(manifest["components"][key]):
            raise ValueError("invalid component hash")
    if not isinstance(manifest["manifest_sha256"], str) or not _SHA.fullmatch(manifest["manifest_sha256"]):
        raise ValueError("invalid manifest hash")
    if manifest != rebuilt:
        raise ValueError("checkpoint integrity mismatch")
    return True
