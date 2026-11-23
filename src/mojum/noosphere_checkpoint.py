"""Versioned atomic metadata references for five-component Noosphere checkpoints.

This module describes a checkpoint; it does not capture component payloads,
persist journals, authenticate manifests, or restore an organism.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "mojum.noosphere.checkpoint/1"
COMPONENTS = ("brain", "genome", "body", "world", "memory")
VISIBILITIES = frozenset(("PUBLIC", "PRIVATE", "RESTRICTED"))
MAX_WIRE_BYTES = 16 * 1024
MAX_TEXT = 256
MAX_TREE_NODES = 2_000
MAX_TREE_DEPTH = 24
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


def _check_native_json_tree(value: Any) -> None:
    """Admit bounded native JSON before any caller-controlled comparison."""
    stack = [(value, 0)]
    seen = set()
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_TREE_NODES or depth > MAX_TREE_DEPTH:
            raise CheckpointError("checkpoint JSON exceeds its structural budget")
        kind = type(item)
        if kind is dict:
            if id(item) in seen:
                raise CheckpointError("checkpoint JSON must not contain cycles or shared containers")
            seen.add(id(item))
            keys = tuple(dict.keys(item))
            if len(keys) > MAX_TREE_NODES - nodes:
                raise CheckpointError("checkpoint object exceeds its node budget")
            if any(type(key) is not str for key in keys):
                raise CheckpointError("checkpoint object keys must be native strings")
            for key in keys:
                try:
                    key.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise CheckpointError("checkpoint keys must be valid UTF-8") from exc
                if len(key) > MAX_WIRE_BYTES:
                    raise CheckpointError("checkpoint key exceeds wire budget")
                stack.append((item[key], depth + 1))
        elif kind is list:
            if id(item) in seen:
                raise CheckpointError("checkpoint JSON must not contain cycles or shared containers")
            seen.add(id(item))
            if len(item) > MAX_TREE_NODES - nodes:
                raise CheckpointError("checkpoint array exceeds its node budget")
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            if len(item) > MAX_WIRE_BYTES:
                raise CheckpointError("checkpoint text exceeds wire budget")
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise CheckpointError("checkpoint text must be valid UTF-8") from exc
        elif kind is int:
            if not -(2**63) <= item <= 2**63 - 1:
                raise CheckpointError("checkpoint integer exceeds signed 64-bit range")
        elif kind is float:
            if not math.isfinite(item):
                raise CheckpointError("checkpoint numbers must be finite")
        elif kind not in (bool, type(None)):
            raise CheckpointError("checkpoint must contain native JSON values")


def _canonical(value: Any) -> bytes:
    _check_native_json_tree(value)
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
    if type(ref) is not dict:
        raise CheckpointError(f"{name} reference has unknown or missing fields")
    keys = tuple(dict.keys(ref))
    if any(type(key) is not str for key in keys) or frozenset(keys) != _COMPONENT_FIELDS:
        raise CheckpointError(f"{name} reference has unknown or missing fields")
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
    _check_native_json_tree(manifest)
    if type(manifest) is not dict:
        raise CheckpointError("checkpoint manifest has unknown or missing fields")
    keys = tuple(dict.keys(manifest))
    if any(type(key) is not str for key in keys) or frozenset(keys) != _ROOT_FIELDS:
        raise CheckpointError("checkpoint manifest has unknown or missing fields")
    if manifest["schema"] != SCHEMA:
        raise CheckpointError("unsupported checkpoint schema")
    agent_id = _text(manifest["agent_id"], "agent_id")
    checkpoint_id = _text(manifest["checkpoint_id"], "checkpoint_id")
    tick = _tick(manifest["tick"])
    previous = manifest["previous_checkpoint_sha256"]
    if previous is not None:
        previous = _digest(previous, "previous_checkpoint_sha256")
    raw_components = manifest["components"]
    if type(raw_components) is not dict:
        raise CheckpointError("checkpoint must reference brain, genome, body, world and memory exactly once")
    component_keys = tuple(dict.keys(raw_components))
    if any(type(key) is not str for key in component_keys) or frozenset(component_keys) != frozenset(COMPONENTS):
        raise CheckpointError("checkpoint must reference brain, genome, body, world and memory exactly once")
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
    _check_native_json_tree(components)
    if type(components) is not dict:
        raise CheckpointError("components must contain the five required names exactly")
    component_keys = tuple(dict.keys(components))
    if any(type(key) is not str for key in component_keys) or frozenset(component_keys) != frozenset(COMPONENTS):
        raise CheckpointError("components must contain the five required names exactly")
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
