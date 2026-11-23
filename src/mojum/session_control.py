"""Versioned, tick-boundary lifecycle control for a synthetic Aquarium session.

This module is a deterministic control-plane value object. It does not run,
pause, stop, or persist an Aquarium process; a caller must invoke transitions
only after the corresponding committed tick has completed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

SCHEMA = "mojum.session-control/1"
TRUST = "checksum_only_not_authenticated"
MAX_EVENTS = 1024
MAX_TICK = 2**63 - 1
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{64}\Z")

CREATED = "created"
RUNNING = "running"
PAUSED = "paused"
TERMINATED = "terminated"

_TRANSITIONS = {
    (CREATED, "start"): RUNNING,
    (RUNNING, "pause"): PAUSED,
    (PAUSED, "resume"): RUNNING,
    (CREATED, "terminate"): TERMINATED,
    (RUNNING, "terminate"): TERMINATED,
    (PAUSED, "terminate"): TERMINATED,
}
_EVENT_FIELDS = {"seq", "event_id", "action", "tick", "from_state", "to_state"}
_SNAPSHOT_FIELDS = {
    "schema", "trust", "organism_id", "state", "last_tick", "events", "revision"
}


class SessionControlError(ValueError):
    """An invalid lifecycle command or inconsistent session snapshot."""


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise SessionControlError(f"{label} must be a 1..128 character ASCII identifier")
    return value


def _tick(value: object, label: str = "tick") -> int:
    if type(value) is not int or not 0 <= value <= MAX_TICK:
        raise SessionControlError(f"{label} must be an integer in [0, {MAX_TICK}]")
    return value


def _canonical_digest(value: dict) -> str:
    try:
        wire = json.dumps(
            value, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise SessionControlError("session record is not canonical JSON") from exc
    return hashlib.sha256(wire).hexdigest()


class SessionController:
    """Pure state machine for start/pause/resume/terminate at committed ticks.

    Every command uses an expected-revision CAS and a caller-provided event ID.
    The revision is a corruption-detection checksum, not an authentication
    signature or distributed lock. The caller remains responsible for enforcing
    a single writer and ensuring no tick is currently executing.
    """

    def __init__(self, organism_id: str):
        self.organism_id = _identifier(organism_id, "organism_id")
        self._state = CREATED
        self._last_tick = 0
        self._events: list[dict] = []
        self._event_ids: set[str] = set()

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_tick(self) -> int:
        return self._last_tick

    @property
    def revision(self) -> str:
        return _canonical_digest(self._record())

    def _record(self) -> dict:
        return {
            "schema": SCHEMA,
            "trust": TRUST,
            "organism_id": self.organism_id,
            "state": self._state,
            "last_tick": self._last_tick,
            "events": copy.deepcopy(self._events),
        }

    def snapshot(self) -> dict:
        """Return a detached, versioned snapshot suitable for external storage."""
        record = self._record()
        return {**record, "revision": _canonical_digest(record)}

    def transition(
        self,
        action: str,
        *,
        event_id: str,
        expected_revision: str,
        committed_tick: int,
        tick_in_progress: bool = False,
    ) -> dict:
        """Apply one lifecycle command after a committed tick boundary.

        The method performs no sleeps, I/O, callbacks, SQLite writes, or process
        signalling. A rejected command leaves the controller byte-for-byte
        unchanged.
        """
        if type(expected_revision) is not str or _REVISION_RE.fullmatch(expected_revision) is None:
            raise SessionControlError("expected_revision must be a lowercase SHA-256 digest")
        if expected_revision != self.revision:
            raise SessionControlError("stale session revision (CAS failed)")
        if type(action) is not str:
            raise SessionControlError("action must be a native string")
        if type(tick_in_progress) is not bool:
            raise SessionControlError("tick_in_progress must be a boolean")
        if tick_in_progress:
            raise SessionControlError("lifecycle transitions are allowed only between committed ticks")
        event_id = _identifier(event_id, "event_id")
        tick = _tick(committed_tick, "committed_tick")
        if tick < self._last_tick:
            raise SessionControlError("committed_tick cannot move backwards")
        if len(self._events) >= MAX_EVENTS:
            raise SessionControlError("session event limit exceeded")
        if event_id in self._event_ids:
            raise SessionControlError("duplicate session event_id")
        next_state = _TRANSITIONS.get((self._state, action))
        if next_state is None:
            raise SessionControlError(f"action {action!r} is invalid while session is {self._state}")

        event = {
            "seq": len(self._events) + 1,
            "event_id": event_id,
            "action": action,
            "tick": tick,
            "from_state": self._state,
            "to_state": next_state,
        }
        # All validation precedes mutation; the following assignments cannot fail.
        self._events.append(event)
        self._event_ids.add(event_id)
        self._state = next_state
        self._last_tick = tick
        return self.snapshot()

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "SessionController":
        """Restore only a complete, internally replayable version-1 snapshot."""
        if type(snapshot) is not dict or set(snapshot) != _SNAPSHOT_FIELDS:
            raise SessionControlError("invalid session snapshot shape")
        if snapshot["schema"] != SCHEMA or snapshot["trust"] != TRUST:
            raise SessionControlError("unsupported session schema or trust claim")
        organism_id = _identifier(snapshot["organism_id"], "organism_id")
        declared_state = snapshot["state"]
        if type(declared_state) is not str or declared_state not in {
            CREATED, RUNNING, PAUSED, TERMINATED
        }:
            raise SessionControlError("invalid declared session state")
        declared_tick = _tick(snapshot["last_tick"], "last_tick")
        events = snapshot["events"]
        if type(events) is not list or len(events) > MAX_EVENTS:
            raise SessionControlError("events must be a bounded native list")
        revision = snapshot["revision"]
        if type(revision) is not str or _REVISION_RE.fullmatch(revision) is None:
            raise SessionControlError("invalid session revision")

        controller = cls(organism_id)
        for index, raw in enumerate(events, start=1):
            if type(raw) is not dict or set(raw) != _EVENT_FIELDS:
                raise SessionControlError(f"invalid event shape at sequence {index}")
            seq = raw["seq"]
            if type(seq) is not int or seq != index:
                raise SessionControlError(f"non-contiguous event sequence at {index}")
            event_id = _identifier(raw["event_id"], f"events[{index}].event_id")
            action = raw["action"]
            if type(action) is not str:
                raise SessionControlError(f"invalid action at sequence {index}")
            tick = _tick(raw["tick"], f"events[{index}].tick")
            if tick < controller._last_tick:
                raise SessionControlError(f"non-monotonic tick at sequence {index}")
            if event_id in controller._event_ids:
                raise SessionControlError(f"duplicate event ID at sequence {index}")
            next_state = _TRANSITIONS.get((controller._state, action))
            if (next_state is None or raw["from_state"] != controller._state
                    or raw["to_state"] != next_state
                    or type(raw["from_state"]) is not str
                    or type(raw["to_state"]) is not str):
                raise SessionControlError(f"invalid transition at sequence {index}")
            controller._events.append({
                "seq": seq,
                "event_id": event_id,
                "action": action,
                "tick": tick,
                "from_state": controller._state,
                "to_state": next_state,
            })
            controller._event_ids.add(event_id)
            controller._state = next_state
            controller._last_tick = tick

        if controller._state != declared_state or controller._last_tick != declared_tick:
            raise SessionControlError("snapshot state or tick does not match event replay")
        if controller.revision != revision:
            raise SessionControlError("session snapshot checksum mismatch")
        return controller
