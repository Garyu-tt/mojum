"""Strict bounded loader for an explicitly NEW synthetic point-room JSON.

Two authoring formats are recognized: the exact scalar ABI for power users,
and a simpler JSON number draft whose scalar types are preserved when
materialized into the *same* exact bootstrap/2. No SQLite or network access.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .room_contract import (
    RoomContractError, decode_room_exact, encode_room_exact,
)
from .world_room import Room, _revision

MAX_ROOM_FILE_BYTES = 1_048_576
MAX_DRAFT_DIMENSION_M = 1000
DRAFT_SCHEMA = "mojum.world.point-room-draft/1"


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RoomContractError("duplicate key in typed-room JSON file")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise RoomContractError("invalid nonfinite JSON token in typed-room file")


def _draft_number(value):
    """No float coercion for room scalars: 4 and 4.0 hash differently."""
    if type(value) is int:
        if not -(2**63) <= value <= (2**63 - 1):
            raise RoomContractError("draft room integer exceeds signed 64-bit budget")
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise RoomContractError("draft room requires finite numeric int or float")


def _decode_room_draft(value):
    if (type(value) is not dict
            or set(value) != {"schema", "width", "height", "obstacles", "start"}
            or value.get("schema") != DRAFT_SCHEMA
            or type(value["obstacles"]) is not list
            or len(value["obstacles"]) > 4096
            or type(value["start"]) is not list
            or len(value["start"]) != 2):
        raise RoomContractError("invalid point-room draft envelope")

    width = _draft_number(value["width"])
    height = _draft_number(value["height"])
    if not (0 < width <= MAX_DRAFT_DIMENSION_M
            and 0 < height <= MAX_DRAFT_DIMENSION_M):
        raise RoomContractError("draft point-room dimensions outside 0..1000m")

    obstacles = []
    for rect in value["obstacles"]:
        if type(rect) is not list or len(rect) != 4:
            raise RoomContractError("draft obstacle must contain exactly four scalars")
        obstacles.append(tuple(_draft_number(x) for x in rect))

    # Starting coordinates are normalized to binary64 by Aquarium, whereas
    # widths/heights and all rectangle components retain their original type.
    start = tuple(float(_draft_number(x)) for x in value["start"])
    try:
        room = Room(width, height, tuple(obstacles))
        # Enforce *the exact same durable ABI* as the existing typed editor:
        # no alternate serialization, new runtime mode or changing old data.
        return decode_room_exact(encode_room_exact(room, start))
    except (ValueError, TypeError, OverflowError) as exc:
        raise RoomContractError("invalid synthetic point-room draft geometry") from exc


def read_new_room_contract(path):
    """Return exact (Room, start) from a local UTF-8 JSON definition.

    Supports original exact-scalar schema and the beginner draft schema.
    Output is always the same canonical typed bootstrap/2 room contract.
    A new SQLite file may be reserved only after this parser succeeds.
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
    if type(value) is dict and value.get("schema") == DRAFT_SCHEMA:
        return _decode_room_draft(value)
    return decode_room_exact(value)


def main(argv=None):
    """Preflight a custom room without creating *any* SQLite journal."""
    parser = argparse.ArgumentParser(
        description="Verify a local synthetic point-room JSON without writing SQLite")
    parser.add_argument("file", type=Path, help="existing local room draft or exact JSON")
    args = parser.parse_args(argv)
    try:
        room, start = read_new_room_contract(args.file)
    except RoomContractError as exc:
        parser.error(str(exc))
    result = {
        "schema": "mojum.world.point-room-preview/1",
        "status": "VALID_SYNTHETIC_POINT_ROOM_NO_SQLITE_CREATED",
        "width_m": room.width,
        "width_type": type(room.width).__name__,
        "height_m": room.height,
        "height_type": type(room.height).__name__,
        "obstacle_count": len(room.obstacles),
        "start": list(start),
        "initial_world_revision": _revision(room, 0, *start),
        "physical_model": "NONE_POINT_ROOM_FIXTURE_NOT_MUJOCO",
    }
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
