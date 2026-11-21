"""Opt-in, lossless numeric room bootstrap for NEW synthetic Aquarium histories.

Legacy bootstrap strings stay byte-identical. This is a versioned additive
contract, not an in-place migration of older ambiguous geometry records.
"""
from __future__ import annotations

import math

from .world_room import Room, _valid

SCHEMA = "mojum.world.room-exact-scalars/1"


class RoomContractError(ValueError):
    """Unknown, ambiguous or non-roundtrippable typed room geometry."""


def _encode_scalar(value):
    if type(value) is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise RoomContractError("room integer exceeds signed 64-bit budget")
        return {"type": "int", "value": value}
    if type(value) is float and math.isfinite(value):
        return {"type": "float", "hex": value.hex()}
    raise RoomContractError("room scalar must be finite original int or float")


def _decode_scalar(item):
    if type(item) is not dict:
        raise RoomContractError("invalid typed room scalar")
    if set(item) == {"type", "value"} and item.get("type") == "int":
        value = item["value"]
        if type(value) is not int or not -(2**63) <= value <= 2**63 - 1:
            raise RoomContractError("invalid typed room integer")
    elif set(item) == {"type", "hex"} and item.get("type") == "float":
        raw = item["hex"]
        if type(raw) is not str or len(raw) > 24:
            raise RoomContractError("invalid typed room float encoding")
        try:
            value = float.fromhex(raw)
        except (ValueError, OverflowError) as exc:
            raise RoomContractError("unparseable typed room float") from exc
    else:
        raise RoomContractError("unknown typed room scalar format")
    if _encode_scalar(value) != item:
        raise RoomContractError("noncanonical typed room scalar")
    return value


def _legacy_room(room, start):
    return {
        "width": float(room.width),
        "height": float(room.height),
        "obstacles": [list(rect) for rect in room.obstacles],
        "start": list(start),
    }


def encode_room_exact(room: Room, start):
    if type(room) is not Room:
        raise RoomContractError("exact Room fixture required")
    if type(start) not in (tuple, list) or len(start) != 2:
        raise RoomContractError("invalid typed room starting coordinates")
    if len(room.obstacles) > 4096:
        raise RoomContractError("typed room obstacle count exceeds bounded decoder")
    if not _valid(room, start[0], start[1]):
        raise RoomContractError("room start lies outside the legal point fixture")
    return {
        "schema": SCHEMA,
        "width": _encode_scalar(room.width),
        "height": _encode_scalar(room.height),
        "obstacles": [
            [_encode_scalar(value) for value in rect]
            for rect in room.obstacles
        ],
        "start": [_encode_scalar(value) for value in start],
    }


def decode_room_exact(contract, *, legacy_room=None):
    """Restore same Python scalar types as world_room._revision hashes.

    legacy_room optionally checks the companion old-style projected room
    bytes to detect inconsistent dual-contract manifests.
    """
    if (type(contract) is not dict
            or set(contract) != {
                "schema", "width", "height", "obstacles", "start",
            } or contract.get("schema") != SCHEMA
            or type(contract["obstacles"]) is not list
            or len(contract["obstacles"]) > 4096
            or type(contract["start"]) is not list
            or len(contract["start"]) != 2):
        raise RoomContractError("invalid exact-room envelope")
    width = _decode_scalar(contract["width"])
    height = _decode_scalar(contract["height"])
    obstacles = []
    for rectangle in contract["obstacles"]:
        if type(rectangle) is not list or len(rectangle) != 4:
            raise RoomContractError("invalid typed obstacle rectangle")
        obstacles.append(tuple(_decode_scalar(x) for x in rectangle))
    start = tuple(_decode_scalar(x) for x in contract["start"])
    # No implicit start int -> float coercion: that would silently change
    # the advertised type contract. Aquarium always stores float starts.
    if any(type(x) is not float for x in start):
        raise RoomContractError("exact-room start must be float after Aquarium normalization")
    try:
        room = Room(width, height, tuple(obstacles))
        if not _valid(room, *start):
            raise RoomContractError("typed room starts in an invalid position")
    except (ValueError, OverflowError, TypeError) as exc:
        raise RoomContractError("invalid typed room geometry") from exc
    if encode_room_exact(room, start) != contract:
        raise RoomContractError("typed room failed canonical roundtrip")
    if legacy_room is not None and (
            type(legacy_room) is not dict
            or _legacy_room(room, start) != legacy_room):
        raise RoomContractError("typed room disagrees with legacy room projection")
    return room, start
