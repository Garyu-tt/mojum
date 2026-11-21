"""Strict bounded loader for an explicitly NEW synthetic typed-room JSON file.

No filesystem mutation or SQLite access. This is NOT a trusted source of
genome data, real robotics geometry, remote content or historic migration.
"""
from __future__ import annotations

import json
from pathlib import Path

from .room_contract import RoomContractError, decode_room_exact

MAX_ROOM_FILE_BYTES = 1_048_576


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RoomContractError("duplicate key in typed-room JSON file")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise RoomContractError("invalid nonfinite JSON token in typed-room file")


def read_new_room_contract(path):
    """Return exact (Room, start) from a local JSON definition.

    A valid definition contains precisely the standalone room_exact object.
    Writing a SQLite bootstrap happens elsewhere, only after CLI preflight
    has separately checked explicit opt-in and an absent destination.
    """
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise RoomContractError("typed-room JSON must be a real local regular file")
    try:
        with source.open("rb") as fp:
            data = fp.read(MAX_ROOM_FILE_BYTES + 1)
    except OSError as exc:
        raise RoomContractError("cannot read typed-room JSON file") from exc
    if not data or len(data) > MAX_ROOM_FILE_BYTES:
        raise RoomContractError("typed-room JSON is empty or exceeds size budget")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise RoomContractError("invalid typed-room JSON content") from exc
    return decode_room_exact(value)
