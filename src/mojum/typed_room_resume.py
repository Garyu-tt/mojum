"""Anchor-pinned, read-only preflight for an existing opt-in typed-room journal.

Reconstructs *only* world geometry and start from the exact bootstrap/2
contract. It does not create, migrate, unlock or append any journal. A later
Aquarium restore must reverify the same independent head to avoid TOCTOU.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .noosphere import LocalNoosphere, NoosphereError
from .room_contract import RoomContractError, decode_room_exact

BOOTSTRAP = "mojum.aquarium.bootstrap/2"
_HEX = frozenset("0123456789abcdef")


class TypedRoomResumeError(ValueError):
    """Missing source, invalid independent head or unexpected typed manifest."""


def load_verified_typed_room(
        database, *, agent_id, expected_journal_head):
    """Return (Room, start, manifest), after complete read-only chain audit.

    The caller must have independently obtained the matching hash head.
    Never treat a hash taken solely from the same SQLite file as an external
    integrity anchor; this is consistency checking, not authentication.
    """
    path = Path(database)
    if (path.is_symlink() or not path.is_file()
            or path.resolve() != path.absolute()):
        raise TypedRoomResumeError(
            "typed-room restore requires existing nonsymlink local SQLite file")
    if (type(expected_journal_head) is not str
            or len(expected_journal_head) != 64
            or any(ch not in _HEX for ch in expected_journal_head)):
        raise TypedRoomResumeError(
            "exact independent 64-hex journal head required")
    if type(agent_id) is not str or not agent_id.strip():
        raise TypedRoomResumeError("exact original agent_id required")

    try:
        with LocalNoosphere(path, agent_id, read_only=True) as database:
            verification = database.verify_chain(
                external_expected_head=expected_journal_head)
            if verification["count"] < 1:
                raise TypedRoomResumeError("typed journal has no bootstrap")
            event = database.event_at(1)["event"]
            if (event.get("topic") != "aquarium.bootstrap"
                    or event.get("event_id") != "aquarium-bootstrap"
                    or type(event.get("payload")) is not dict
                    or event["payload"].get("schema") != BOOTSTRAP
                    or event["payload"].get("agent_id") != agent_id
                    or "room_exact" not in event["payload"]
                    or "room" not in event["payload"]):
                raise TypedRoomResumeError(
                    "not a versioned typed-room bootstrap/2 journal")
            manifest = event["payload"]
            room, start = decode_room_exact(
                manifest["room_exact"], legacy_room=manifest["room"])
            if (database.count() != verification["count"]
                    or database.head() != expected_journal_head):
                raise TypedRoomResumeError(
                    "journal advanced after read-only typed-room audit")
    except (NoosphereError, sqlite3.Error, OSError) as exc:
        raise TypedRoomResumeError(
            "typed-room source owner/hash-chain verification failed") from exc
    except RoomContractError as exc:
        raise TypedRoomResumeError("typed-room geometry contract invalid") from exc
    return room, start, manifest
