"""Load a validated versioned point-room draft into the synthetic runtime.

The offline designer's exact JSON tokens are validated before conversion to
Python float coordinates. The Aquarium bootstrap pins the *runtime geometry*,
not the raw draft bytes; this module does not mutate any journal.
"""
from __future__ import annotations

from pathlib import Path

from .room_spec import (
    DRAFT_SCHEMA, MAX_INPUT_BYTES, RoomSpecError, _parse,
    validate_room_document,
)
from .world_room import Room, initial_state


def load_runtime_room(path: str | Path) -> tuple[Room, tuple[float, float]]:
    """Fail closed before SQLite birth on invalid, oversized or unsafe drafts."""
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise RoomSpecError(f"cannot read room draft: {exc}") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise RoomSpecError("room document exceeds byte limit")

    document = _parse(raw)
    report = validate_room_document(document)
    if not report.valid:
        raise RoomSpecError("invalid room draft: " + "; ".join(report.errors[:5]))
    # The validator can also inspect unversioned legacy geometry. Starting a
    # new journal from an authored file requires an explicit version.
    if document.get("schema") != DRAFT_SCHEMA:
        raise RoomSpecError(f"room draft requires schema {DRAFT_SCHEMA}")

    try:
        room = Room(
            float(document["width"]), float(document["height"]),
            tuple(tuple(float(value) for value in rect)
                  for rect in document["obstacles"]),
        )
        start = tuple(float(value) for value in document["start"])
        # The strict Decimal validator also checks rounded float boundaries;
        # exercise the real point-room start gate before creating any journal.
        initial_state(room, *start)
    except (ValueError, TypeError, OverflowError) as exc:
        raise RoomSpecError("room draft cannot initialize the point-room runtime") from exc
    return room, start
