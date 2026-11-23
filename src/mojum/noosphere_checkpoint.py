"""Versioned atomic metadata references for five-component Noosphere checkpoints.

This module describes a checkpoint; it does not capture component payloads,
persist journals, authenticate manifests, or restore an organism.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA = "mojum.noosphere.checkpoint/1"
COMPONENTS = ("brain", "genome", "body", "world", "memory")
VISIBILITIES = frozenset(("PUBLIC", "PRIVATE", "RESTRICTED"))
MAX_WIRE_BYTES = 16 * 1024
MAX_TEXT = 256
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT_FIELDS = {
    "schema", "revision", "content_sha256", "visibility", "agent_id", "tick",
}
_ROOT_FIELDS = {
    "schema", "agent_id", "checkpoint_id", "tick",
    "previous_checkpoint_sha256", "components", "checkpoint_sha256",
}


class CheckpointError(ValueError):
    """Raised when a checkpoint reference set is incomplete or inconsistent."""


def _exact_keys(value: Any, expected: set[str], field: str) -> None:
    if type(value) is not dict:
        raise CheckpointError(f"{field} must be an exact dictionary")
    keys = tuple(dict.keys(value))
    if any(type(key) is not str for key in keys):
        raise CheckpointError(f"{field} keys must be exact strings")
    if set(keys) != expected:
        raise CheckpointError(f"{field} has unknown or missing fields")


def _text(value: Any, field: str, limit: int = MAX_TEXT) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise CheckpointError(f"{field} must be nonempty bounded text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise CheckpointError(f"{field} must be valid UTF-8") from exc
    return value


def _tick(value: Any, field: str = "tick") -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise CheckpointError(f"{field} must be a nonnegative signed-64-bit integer")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise CheckpointError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _canonical(value: Any) -> bytes:
    try:
        wire = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        raw = wire.encode("utf-8", errors="strict")
        if len(raw) > MAX_WIRE_BYTES:
            raise CheckpointError("checkpoint exceeds 16 KiB")
        if json.loads(wire) != value:
            raise CheckpointError("checkpoint is not lossless canonical JSON")
        return raw
    except CheckpointError:
        raise
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise CheckpointError("checkpoint is not canonical UTF-8 JSON") from exc


def _component(ref: Any, name: str, agent_id: str, tick: int) -> dict:
    _exact_keys(ref, _COMPONENT_FIELDS, f"{name} reference")
    checked = {
        "schema": _text(ref["schema"], f"{name}.schema", 128),
        "revision": _text(ref["revision"], f"{name}.revision"),
        "content_sha256": _digest(ref["content_sha256"], f"{name}.content_sha256"),
        "visibility": ref["visibility"],
        "agent_id": _text(ref["agent_id"], f"{name}.agent_id"),
        "tick": _tick(ref["tick"], f"{name}.tick"),
    }
    if type(checked["visibility"]) is not str or checked["visibility"] not in VISIBILITIES:
        raise CheckpointError(f"{name}.visibility is unsupported")
    if checked["agent_id"] != agent_id:
        raise CheckpointError(f"{name} belongs to another organism")
    if checked["tick"] != tick:
        raise CheckpointError(f"{name} was captured at a different causal tick")
    return checked


def _payload(manifest: dict) -> dict:
    return {key: manifest[key] for key in (
        "schema", "agent_id", "checkpoint_id", "tick",
        "previous_checkpoint_sha256", "components",
    )}


def _validate_manifest(manifest: Any) -> dict:
    _exact_keys(manifest, _ROOT_FIELDS, "checkpoint manifest")
    schema = manifest["schema"]
    if type(schema) is not str or schema != SCHEMA:
        raise CheckpointError("unsupported checkpoint schema")
    agent_id = _text(manifest["agent_id"], "agent_id")
    checkpoint_id = _text(manifest["checkpoint_id"], "checkpoint_id")
    tick = _tick(manifest["tick"])
    previous = manifest["previous_checkpoint_sha256"]
    if previous is not None:
        previous = _digest(previous, "previous_checkpoint_sha256")
    raw_components = manifest["components"]
    _exact_keys(raw_components, set(COMPONENTS), "checkpoint components")
    components = {
        name: _component(raw_components[name], name, agent_id, tick)
        for name in COMPONENTS
    }
    result = {
        "schema": SCHEMA,
        "agent_id": agent_id,
        "checkpoint_id": checkpoint_id,
        "tick": tick,
        "previous_checkpoint_sha256": previous,
        "components": components,
    }
    expected = hashlib.sha256(_canonical(result)).hexdigest()
    if _digest(manifest["checkpoint_sha256"], "checkpoint_sha256") != expected:
        raise CheckpointError("checkpoint manifest digest mismatch")
    return {**result, "checkpoint_sha256": expected}


def create_checkpoint(
    agent_id: str,
    checkpoint_id: str,
    tick: int,
    components: dict,
    *,
    previous_checkpoint_sha256: str | None = None,
) -> dict:
    """Create a detached manifest from five component references.

    Each ref has exactly: schema, revision, content_sha256, visibility,
    agent_id and tick. A predecessor digest is optional only for a genesis
    checkpoint. The return value contains no component payload bytes.
    """
    agent = _text(agent_id, "agent_id")
    identifier = _text(checkpoint_id, "checkpoint_id")
    at_tick = _tick(tick)
    if previous_checkpoint_sha256 is not None:
        previous_checkpoint_sha256 = _digest(
            previous_checkpoint_sha256, "previous_checkpoint_sha256"
        )
    _exact_keys(components, set(COMPONENTS), "components")
    checked = {
        name: _component(components[name], name, agent, at_tick)
        for name in COMPONENTS
    }
    body = {
        "schema": SCHEMA,
        "agent_id": agent,
        "checkpoint_id": identifier,
        "tick": at_tick,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "components": checked,
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    manifest = {**body, "checkpoint_sha256": digest}
    _canonical(manifest)
    return manifest


def verify_checkpoint(
    manifest: dict,
    *,
    expected_previous_checkpoint_sha256: str | None = None,
) -> dict:
    """Validate and detach a manifest, optionally enforcing predecessor CAS.

    Supplying the expected predecessor is mandatory for a caller that needs
    optimistic concurrency. It must equal the manifest's declared predecessor;
    this pure helper does not store or advance any authoritative head.
    """
    if expected_previous_checkpoint_sha256 is not None:
        _digest(expected_previous_checkpoint_sha256, "expected_previous_checkpoint_sha256")
    checked = _validate_manifest(manifest)
    if checked["previous_checkpoint_sha256"] != expected_previous_checkpoint_sha256:
        raise CheckpointError("stale checkpoint predecessor (CAS failed)")
    return checked


def encode_checkpoint(manifest: dict) -> bytes:
    """Return canonical bounded UTF-8 JSON after full validation."""
    checked = _validate_manifest(manifest)
    return _canonical(checked)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointError("duplicate JSON object key")
        result[key] = value
    return result


def decode_checkpoint(wire: bytes | str) -> dict:
    """Decode a bounded canonical manifest; duplicate keys are rejected."""
    if type(wire) is bytes:
        if len(wire) > MAX_WIRE_BYTES:
            raise CheckpointError("checkpoint exceeds 16 KiB")
        try:
            raw = wire.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CheckpointError("checkpoint must be UTF-8") from exc
    elif type(wire) is str:
        try:
            if len(wire.encode("utf-8", errors="strict")) > MAX_WIRE_BYTES:
                raise CheckpointError("checkpoint exceeds 16 KiB")
            raw = wire
        except UnicodeEncodeError as exc:
            raise CheckpointError("checkpoint must be UTF-8") from exc
    else:
        raise CheckpointError("checkpoint wire must be bytes or text")
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
    except CheckpointError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise CheckpointError("malformed checkpoint JSON") from exc
    checked = _validate_manifest(parsed)
    if _canonical(checked).decode("utf-8") != raw:
        raise CheckpointError("checkpoint JSON is not canonical")
    return checked
