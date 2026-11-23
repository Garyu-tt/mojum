"""Deterministic policy contract for isolating optional-world adapter failures.

This module does not supervise processes or wire itself into Aquarium. It only
computes a fail-closed, replayable decision from explicit synthetic events.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping

SCHEMA = "mojum.optional-world-failover/1"
_ALLOWED_MODES = frozenset({"disabled", "active", "quarantined"})
_ALLOWED_ERRORS = frozenset({"timeout", "unavailable", "invalid_frame", "protocol_error"})
_MAX_ID = 96
_MAX_EVENT_ID = 128
_MAX_REVISION = 2**63 - 1


class FailoverError(ValueError):
    """Invalid, stale, or unsafe failover input."""


@dataclass(frozen=True, slots=True)
class FailoverState:
    adapter_id: str
    mode: str = "disabled"
    revision: int = 0
    last_tick: int = -1
    failure_count: int = 0
    last_event_id: str = ""

    @property
    def selected_world(self) -> str:
        """The home room is always available; external mode is explicitly active."""
        return "external_adapter" if self.mode == "active" else "home_room"


def initial_state(adapter_id: str) -> FailoverState:
    _identifier(adapter_id, "adapter_id", _MAX_ID)
    return FailoverState(adapter_id=adapter_id)


def _identifier(value: object, field: str, limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise FailoverError(f"{field} must be a non-empty native string of at most {limit} characters")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
        raise FailoverError(f"{field} must contain printable ASCII without spaces")
    return value


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FailoverError(f"{field} must be a native integer in [{minimum}, {maximum}]")
    return value


def _validate_state(state: object) -> FailoverState:
    if type(state) is not FailoverState:
        raise FailoverError("state must be an exact FailoverState")
    _identifier(state.adapter_id, "adapter_id", _MAX_ID)
    if type(state.mode) is not str or state.mode not in _ALLOWED_MODES:
        raise FailoverError("invalid adapter mode")
    _integer(state.revision, "revision", 0, _MAX_REVISION)
    _integer(state.last_tick, "last_tick", -1, _MAX_REVISION)
    _integer(state.failure_count, "failure_count", 0, _MAX_REVISION)
    if type(state.last_event_id) is not str:
        raise FailoverError("last_event_id must be a native string")
    if state.last_event_id:
        _identifier(state.last_event_id, "last_event_id", _MAX_EVENT_ID)
    if state.revision == 0 and (state.last_tick != -1 or state.failure_count != 0 or state.last_event_id):
        raise FailoverError("initial state fields are inconsistent")
    if state.revision > 0 and (state.last_tick < 0 or not state.last_event_id):
        raise FailoverError("advanced state is missing its event cursor")
    return state


def _canonical(state: FailoverState) -> bytes:
    payload = {
        "schema": SCHEMA,
        "adapter_id": state.adapter_id,
        "mode": state.mode,
        "revision": state.revision,
        "last_tick": state.last_tick,
        "failure_count": state.failure_count,
        "last_event_id": state.last_event_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def snapshot_state(state: FailoverState) -> dict[str, object]:
    state = _validate_state(state)
    payload = json.loads(_canonical(state).decode("ascii"))
    payload["sha256"] = hashlib.sha256(_canonical(state)).hexdigest()
    return payload


def restore_state(raw: object) -> FailoverState:
    if type(raw) is not dict:
        raise FailoverError("snapshot must be a native dict")
    if any(type(key) is not str for key in raw):
        raise FailoverError("snapshot keys must be native strings")
    expected = {
        "schema", "adapter_id", "mode", "revision", "last_tick",
        "failure_count", "last_event_id", "sha256",
    }
    if set(raw) != expected:
        raise FailoverError("snapshot fields do not match schema")
    if type(raw["schema"]) is not str or raw["schema"] != SCHEMA:
        raise FailoverError("unsupported snapshot schema")
    digest = raw["sha256"]
    if type(digest) is not str or len(digest) != 64:
        raise FailoverError("invalid snapshot digest")
    state = FailoverState(
        adapter_id=raw["adapter_id"], mode=raw["mode"], revision=raw["revision"],
        last_tick=raw["last_tick"], failure_count=raw["failure_count"],
        last_event_id=raw["last_event_id"],
    )
    state = _validate_state(state)
    if not hashlib.sha256(_canonical(state)).hexdigest() == digest:
        raise FailoverError("snapshot digest mismatch")
    return state


def transition(state: FailoverState, event: object) -> FailoverState:
    """Apply one explicit event; stale or implicit recovery is rejected."""
    state = _validate_state(state)
    if type(event) is not dict:
        raise FailoverError("event must be a native dict")
    if any(type(key) is not str for key in event):
        raise FailoverError("event keys must be native strings")

    common = {"schema", "event_id", "adapter_id", "tick", "expected_revision", "kind"}
    kind = event.get("kind")
    if type(kind) is not str:
        raise FailoverError("kind must be a native string")
    optional = {
        "enable": {"capability_granted"},
        "disable": set(),
        "failure": {"error_code"},
        "healthy": set(),
    }
    if kind not in optional or set(event) != common | optional[kind]:
        raise FailoverError("event fields do not match the selected event kind")
    if type(event["schema"]) is not str or event["schema"] != SCHEMA:
        raise FailoverError("unsupported event schema")
    event_id = _identifier(event["event_id"], "event_id", _MAX_EVENT_ID)
    adapter_id = _identifier(event["adapter_id"], "adapter_id", _MAX_ID)
    if adapter_id != state.adapter_id:
        raise FailoverError("event adapter_id does not match state")
    tick = _integer(event["tick"], "tick", 0, _MAX_REVISION)
    expected_revision = _integer(event["expected_revision"], "expected_revision", 0, _MAX_REVISION)
    if expected_revision != state.revision:
        raise FailoverError("stale expected_revision")
    if state.revision >= _MAX_REVISION or tick <= state.last_tick:
        raise FailoverError("revision exhausted or tick is not strictly increasing")
    if event_id == state.last_event_id:
        raise FailoverError("duplicate event_id")

    mode = state.mode
    failures = state.failure_count
    if kind == "enable":
        granted = event["capability_granted"]
        if type(granted) is not bool or granted is not True:
            raise FailoverError("enable requires explicit capability_granted=true")
        if mode == "active":
            raise FailoverError("adapter is already active")
        mode, failures = "active", 0
    elif kind == "disable":
        mode = "disabled"
    elif kind == "failure":
        error_code = event["error_code"]
        if type(error_code) is not str or error_code not in _ALLOWED_ERRORS:
            raise FailoverError("error_code is not allowlisted")
        if mode != "active":
            raise FailoverError("only an active adapter can report a failure")
        mode, failures = "quarantined", failures + 1
    else:  # healthy
        if mode != "active":
            raise FailoverError("health events cannot implicitly recover a quarantined adapter")
        failures = 0

    return FailoverState(
        adapter_id=state.adapter_id, mode=mode, revision=state.revision + 1,
        last_tick=tick, failure_count=failures, last_event_id=event_id,
    )


def replay(initial: FailoverState, events: Iterable[Mapping[str, object]]) -> FailoverState:
    """Deterministically replay a caller-owned finite event sequence."""
    state = _validate_state(initial)
    for event in events:
        state = transition(state, event)
    return state
