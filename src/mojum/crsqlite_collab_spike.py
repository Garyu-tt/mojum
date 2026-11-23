"""Disposable CRDT convergence oracle for PUBLIC_SYNTHETIC UI layout only.

Research fixture for #759. Not an implementation of cr-sqlite's merge algorithm,
a peer-authentication layer, a transport, or a canonical Noosphere store.
"""
from __future__ import annotations

import hashlib
import json
import re

WIRE = "mojum.crsqlite-ui-fixture/1"
VISIBILITY = "PUBLIC_SYNTHETIC"
MAX_EVENTS = 2048
MAX_JSON_BYTES = 256 * 1024
_FIELDS = frozenset({"wire", "visibility", "actor", "counter", "key", "value"})
_ACTOR = re.compile(r"r[1-9][0-9]{0,4}", re.ASCII)
_KEY = re.compile(r"view\.v[1-9][0-9]{0,3}\.(x|y|collapsed)", re.ASCII)


def validate_event(event: object) -> dict:
    """Copy a restricted, explicitly synthetic event without coercions."""
    if type(event) is not dict or set(event) != _FIELDS:
        raise ValueError("invalid CRDT event fields")
    if (type(event["wire"]) is not str or event["wire"] != WIRE
            or type(event["visibility"]) is not str
            or event["visibility"] != VISIBILITY):
        raise PermissionError("only PUBLIC_SYNTHETIC UI fixtures are allowed")
    actor, counter, key, value = (
        event["actor"], event["counter"], event["key"], event["value"]
    )
    if type(actor) is not str or _ACTOR.fullmatch(actor) is None:
        raise ValueError("invalid pseudonymous replica")
    if type(counter) is not int or not 1 <= counter <= 1_000_000:
        raise ValueError("invalid logical counter")
    if type(key) is not str:
        raise ValueError("invalid UI key")
    match = _KEY.fullmatch(key)
    if match is None:
        raise PermissionError("field is outside ephemeral UI layout allowlist")
    if value is not None:
        if match.group(1) == "collapsed":
            if type(value) is not bool:
                raise ValueError("collapsed must be boolean")
        elif type(value) is not int or not -2048 <= value <= 2048:
            raise ValueError("coordinates must be bounded integers")
    return dict(event)


def event(actor: str, counter: int, key: str, value: object) -> dict:
    return validate_event({
        "wire": WIRE, "visibility": VISIBILITY, "actor": actor,
        "counter": counter, "key": key, "value": value,
    })


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def decode_events(data: bytes) -> list[dict]:
    """Bounded JSON input. No arbitrary nested objects, floats or extra keys."""
    if type(data) is not bytes or len(data) > MAX_JSON_BYTES:
        raise ValueError("invalid or oversized event frame")
    try:
        decoded = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (UnicodeError, RecursionError, TypeError, ValueError) as exc:
        raise ValueError("malformed event frame") from exc
    if type(decoded) is not list or len(decoded) > MAX_EVENTS:
        raise ValueError("invalid or oversized event list")
    return [validate_event(item) for item in decoded]


class Replica:
    """Bounded grow-only event ledger with deterministic LWW view projection.

    This is an independent synthetic oracle, NOT authoritative CR-SQLite.
    Equal logical counter from different actors is resolved by actor id. A
    duplicate (actor, counter) with different bytes is rejected as equivocation.
    Atomic merge means malformed batches never partly change the replica.
    """

    def __init__(self) -> None:
        self._events: dict[tuple[str, int], dict] = {}

    def merge(self, batch: list[dict] | tuple[dict, ...]) -> None:
        if type(batch) not in (list, tuple) or len(batch) > MAX_EVENTS:
            raise ValueError("invalid or oversized merge batch")
        staged = dict(self._events)
        for item in batch:
            op = validate_event(item)
            op_id = (op["actor"], op["counter"])
            previous = staged.get(op_id)
            if previous is not None and previous != op:
                raise ValueError("conflicting event with same actor/counter")
            staged[op_id] = op
            if len(staged) > MAX_EVENTS:
                raise ValueError("replica event budget exceeded")
        # Reject atomically if the resulting replica would be impossible to
        # export within the same wire budget. Otherwise retransmission could
        # leave a permanently non-serializable (but seemingly valid) state.
        canonical = _canonical([op for _, op in sorted(staged.items())])
        if len(canonical) > MAX_JSON_BYTES:
            raise ValueError("replica serialization budget exceeded")
        self._events = staged

    def merge_frame(self, data: bytes) -> None:
        self.merge(decode_events(data))

    def events(self) -> list[dict]:
        return [dict(op) for _, op in sorted(self._events.items())]

    def frame(self) -> bytes:
        frame = _canonical(self.events())
        if len(frame) > MAX_JSON_BYTES:
            raise ValueError("serialized frame budget exceeded")
        return frame

    def view(self) -> dict[str, object]:
        winners: dict[str, dict] = {}
        for op in self._events.values():
            key = op["key"]
            previous = winners.get(key)
            if previous is None or (
                op["counter"], op["actor"]
            ) > (previous["counter"], previous["actor"]):
                winners[key] = op
        return {
            key: op["value"] for key, op in sorted(winners.items())
            if op["value"] is not None
        }

    def digest(self) -> str:
        return hashlib.sha256(self.frame()).hexdigest()


def partition_fixture() -> dict:
    """Reordering, duplicate retransmission and bidirectional convergence."""
    a = [
        event("r1", 1, "view.v1.x", 10),
        event("r1", 2, "view.v1.collapsed", True),
        event("r1", 3, "view.v2.y", 12),
    ]
    b = [
        event("r2", 1, "view.v1.x", -5),
        event("r2", 4, "view.v2.y", None),
        event("r2", 3, "view.v2.x", 25),
    ]
    left, right = Replica(), Replica()
    left.merge(a)
    right.merge(b)
    left.merge(b + b)
    right.merge(list(reversed(a)))
    if left.digest() != right.digest() or left.view() != right.view():
        raise AssertionError("CRDT oracle did not converge")
    return {
        "wire": WIRE, "event_count": len(left.events()),
        "digest": left.digest(), "view": left.view(), "converged": True,
    }


if __name__ == "__main__":
    print(json.dumps(partition_fixture(), sort_keys=True))
