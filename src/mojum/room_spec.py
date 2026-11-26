"""Read-only strict validation of typed point-room JSON documents.

The offline room designer emits a JSON draft that must be checked before it is
turned into a PRIVATE journal. Legacy manifests hash the *exact* JSON number
tokens of the built-in room, so a validator that quietly re-parses ``0.1`` as a
Python float can accept geometry whose revision will differ. This module keeps
every number token verbatim while validating shape, bounds and start placement.

Nothing is written: no SQLite, no journal, no world mutation, no obstacle
oracle for a running organism. Validation is deterministic and CPU-bounded.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Context, Decimal, InvalidOperation, localcontext
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

REPORT_SCHEMA = "mojum.room-spec-report/1"
MAX_INPUT_BYTES = 256 * 1024
MAX_OBSTACLES = 4096
# Keep exact wire spelling, but bound Decimal parsing and arithmetic on hostile inputs.
MAX_NUMBER_TOKEN_CHARS = 128
# Every admitted nonzero coordinate converts to a finite nonzero binary64:
# its leading decimal digit is between 10**-324 and 10**308. Allow the full
# token length below that digit, twice the span for products, and carry digits
# for the sum of all obstacle areas.
_GEOMETRY_PRECISION = 2 * (309 + 324 + MAX_NUMBER_TOKEN_CHARS) + len(str(MAX_OBSTACLES))
_GEOMETRY_CONTEXT = Context(prec=_GEOMETRY_PRECISION, Emin=-2000, Emax=2000)
# Pairwise overlap analysis is bounded so a huge but legal document cannot
# turn validation into quadratic work on a CPU-first fixture.
MAX_OVERLAP_ANALYSIS_OBSTACLES = 256
REQUIRED_KEYS = frozenset(("width", "height", "obstacles", "start"))
# The offline room designer always stamps its draft with this schema key
# (``tools/room_designer_core.js``). It is not geometry, so accept exactly this
# value in addition to the four required keys; anything else stays unexpected.
DRAFT_SCHEMA = "mojum.world.point-room-draft/1"

_NUMBER = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z", re.ASCII
)


class RoomSpecError(ValueError):
    """Malformed input that cannot yield an inspectable document."""


class NumberToken(str):
    """Exact JSON number spelling, distinguishable from a JSON string."""

    __slots__ = ()


def _duplicate_guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RoomSpecError("duplicate JSON key: %s" % (key,))
        result[key] = value
    return result


def _reject_constant(value):
    raise RoomSpecError("non-finite JSON number is not permitted: %s" % (value,))


def _parse(raw: bytes):
    if type(raw) is not bytes:
        raise RoomSpecError("room document must be supplied as bytes")
    if len(raw) > MAX_INPUT_BYTES:
        raise RoomSpecError("room document exceeds byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RoomSpecError("room document is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_guard,
            parse_constant=_reject_constant,
            parse_int=NumberToken,
            parse_float=NumberToken,
        )
    except RoomSpecError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise RoomSpecError("room document is not valid JSON") from exc


def _as_number(value, label, errors):
    """Return a Decimal for an exact number token, or record a shape error."""
    if type(value) is not NumberToken:
        errors.append("%s must be a JSON number, not %s" % (label, type(value).__name__))
        return None
    if _NUMBER.fullmatch(value) is None:
        errors.append("%s has an invalid JSON number token" % (label,))
        return None
    if len(value) > MAX_NUMBER_TOKEN_CHARS:
        errors.append("%s exceeds the number-token length limit" % (label,))
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        errors.append("%s is not a finite decimal" % (label,))
        return None
    if not number.is_finite():
        errors.append("%s is not a finite decimal" % (label,))
        return None
    # Zero tokens can carry arbitrary exponents. Use plain zero in geometry;
    # the report retains the authored spelling directly from the document.
    if number.is_zero():
        return Decimal(0)
    # Point-room runtime coordinates are Python numeric scalars. Reject JSON
    # values whose magnitude would become infinity or a nonzero underflow on
    # conversion, rather than validating an unusable room or overflowing when
    # computing areas. The original token is still used in the report.
    try:
        as_float = float(number)
    except (OverflowError, ValueError):
        as_float = math.inf
    if not math.isfinite(as_float) or as_float == 0:
        errors.append("%s is not representable as a finite room coordinate" % (label,))
        return None
    return number


def _as_pair(value, label, errors):
    if type(value) is not list or len(value) != 2:
        errors.append("%s must be a list of exactly two numbers" % (label,))
        return None
    pair = tuple(_as_number(item, "%s[%d]" % (label, index), errors)
                 for index, item in enumerate(value))
    return None if any(item is None for item in pair) else pair


def _as_rect(value, label, errors):
    if type(value) is not list or len(value) != 4:
        errors.append("%s must be a list of exactly four numbers" % (label,))
        return None
    rect = tuple(_as_number(item, "%s[%d]" % (label, index), errors)
                 for index, item in enumerate(value))
    return None if any(item is None for item in rect) else rect


def _overlap(first, second):
    """Closed-rectangle overlap, matching the runtime's inclusive blocking."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax <= bx + bw and bx <= ax + aw and ay <= by + bh and by <= ay + ah


def _runtime_geometry_errors(document, start):
    """Match native integer legacy rooms and float-only versioned drafts.

    A legacy unversioned Room preserves JSON integer geometry, while the
    versioned room-draft loader converts all coordinates to float. Do not
    falsely accept a versioned draft that will fail at bootstrap, or falsely
    reject a valid unversioned native-integer room.
    """
    # The versioned draft loader constructs an all-float Room. Legacy
    # unversioned inputs can be loaded directly by world_room.Room, which
    # preserves native JSON integer geometry. Validate each against its actual
    # downstream numeric contract rather than conflating these two paths.
    versioned_draft = document.get("schema") == DRAFT_SCHEMA

    def runtime_number(token):
        if versioned_draft or any(ch in token for ch in ".eE"):
            return float(token)
        return int(token)

    errors = []
    width, height = runtime_number(document["width"]), runtime_number(document["height"])
    for index, rect in enumerate(document["obstacles"]):
        x, y, w, h = (runtime_number(value) for value in rect)
        if not (w > 0 and h > 0):
            errors.append(
                "obstacles[%d] collapses to a non-positive size after runtime "
                "float conversion" % (index,)
            )
            continue
        if x < 0 or y < 0 or x + w > width or y + h > height:
            errors.append(
                "obstacles[%d] lies outside the room after runtime float "
                "conversion" % (index,)
            )
    if start is not None:
        x_f, y_f = float(start[0]), float(start[1])
        if not (0 <= x_f <= width and 0 <= y_f <= height):
            errors.append(
                "start lies outside the room after runtime float conversion"
            )
        else:
            for index, rect in enumerate(document["obstacles"]):
                x, y, w, h = (runtime_number(value) for value in rect)
                if x <= x_f <= x + w and y <= y_f <= y + h:
                    errors.append(
                        "start lies inside obstacles[%d] after runtime float "
                        "conversion (closed boundaries block)" % (index,)
                    )
                    break
    return errors

def validate_room_document(document):
    """Validate a parsed room document. Returns a :class:`RoomReport`."""
    with localcontext(_GEOMETRY_CONTEXT):
        return _validate_geometry(document)


def _validate_geometry(document):
    errors = []
    warnings = []

    if type(document) is not dict:
        return RoomReport(("room document root must be a JSON object",), (), None)

    keys = set(document)
    missing = sorted(REQUIRED_KEYS - keys)
    unexpected = sorted(keys - REQUIRED_KEYS - {"schema"})
    if missing:
        errors.append("missing room key(s): %s" % (", ".join(missing),))
    if unexpected:
        errors.append("unexpected room key(s): %s" % (", ".join(unexpected),))
    if "schema" in document and document["schema"] != DRAFT_SCHEMA:
        errors.append("unsupported room schema: %r" % (document["schema"],))
    if errors:
        return RoomReport(tuple(errors), (), None)

    width = _as_number(document["width"], "width", errors)
    height = _as_number(document["height"], "height", errors)
    if width is not None and width <= 0:
        errors.append("width must be greater than zero")
        width = None
    if height is not None and height <= 0:
        errors.append("height must be greater than zero")
        height = None

    obstacles_value = document["obstacles"]
    obstacles = []
    if type(obstacles_value) is not list:
        errors.append("obstacles must be a JSON list")
    elif len(obstacles_value) > MAX_OBSTACLES:
        errors.append("obstacles exceeds the %d rectangle budget" % (MAX_OBSTACLES,))
    else:
        for index, item in enumerate(obstacles_value):
            label = "obstacles[%d]" % (index,)
            rect = _as_rect(item, label, errors)
            if rect is None:
                continue
            x, y, w, h = rect
            if w <= 0 or h <= 0:
                errors.append("%s must have positive width and height" % (label,))
                continue
            if x < 0 or y < 0:
                errors.append("%s lies outside the room" % (label,))
                continue
            if width is not None and x + w > width:
                errors.append("%s lies outside the room" % (label,))
                continue
            if height is not None and y + h > height:
                errors.append("%s lies outside the room" % (label,))
                continue
            obstacles.append(rect)

    start = _as_pair(document["start"], "start", errors)
    if start is not None:
        sx, sy = start
        if width is not None and not 0 <= sx <= width:
            errors.append("start.x must lie within [0, width]")
            start = None
        if height is not None and not 0 <= sy <= height:
            errors.append("start.y must lie within [0, height]")
            start = None
    if start is not None:
        for index, (x, y, w, h) in enumerate(obstacles):
            if x <= start[0] <= x + w and y <= start[1] <= y + h:
                errors.append(
                    "start lies inside obstacles[%d] (closed boundaries block)"
                    % (index,)
                )
                start = None
                break

    # Exact tokens are authoritative for replay, but a draft is only usable if
    # the runtime float room can load it. Only run this once the exact shape is
    # fully legal, so existing precise error messages stay first and stable.
    if not errors:
        errors.extend(_runtime_geometry_errors(document, start))

    if len(obstacles) > MAX_OVERLAP_ANALYSIS_OBSTACLES:
        warnings.append(
            "overlap analysis skipped above %d obstacles"
            % (MAX_OVERLAP_ANALYSIS_OBSTACLES,)
        )
    else:
        for later in range(len(obstacles)):
            for earlier in range(later):
                if obstacles[earlier] == obstacles[later]:
                    warnings.append(
                        "obstacles[%d] duplicates obstacles[%d]"
                        % (later, earlier)
                    )
                elif _overlap(obstacles[earlier], obstacles[later]):
                    warnings.append(
                        "obstacles[%d] overlaps obstacles[%d]"
                        % (earlier, later)
                    )

    if errors:
        return RoomReport(tuple(errors), tuple(warnings), None)

    areas = [w * h for _, _, w, h in obstacles]
    summary = {
        "width": str(document["width"]),
        "height": str(document["height"]),
        "obstacle_count": len(obstacles),
        "obstacles": [[str(v) for v in rect] for rect in document["obstacles"]],
        "start": [str(v) for v in document["start"]],
        "obstacles_total_area": str(sum(areas, Decimal(0))),
        "estimated_free_area": str(width * height - sum(areas, Decimal(0))),
        "number_tokens_preserved": True,
    }
    return RoomReport((), tuple(warnings), summary)


@dataclass(frozen=True)
class RoomReport:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: dict | None

    @property
    def valid(self):
        return not self.errors

    def as_json(self):
        return {
            "schema": REPORT_SCHEMA,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "summary": self.summary,
        }


def validate_room_bytes(raw: bytes):
    """Parse and validate raw document bytes without raising on bad input."""
    try:
        document = _parse(raw)
    except RoomSpecError as exc:
        return RoomReport((str(exc),), (), None)
    return validate_room_document(document)


def _text_report(report):
    lines = []
    if report.valid:
        summary = report.summary
        lines.append(
            "VALID: width=%s height=%s obstacles=%d start=(%s, %s)"
            % (
                summary["width"],
                summary["height"],
                summary["obstacle_count"],
                summary["start"][0],
                summary["start"][1],
            )
        )
    else:
        lines.append("INVALID: %d error(s)" % (len(report.errors),))
        lines.extend("error: %s" % (message,) for message in report.errors)
    lines.extend("warning: %s" % (message,) for message in report.warnings)
    return "\n".join(lines)


def _read_room_file(path):
    """Read a bounded regular descriptor without waiting for a FIFO writer."""
    if os.name == "posix" and not getattr(os, "O_NONBLOCK", 0):
        raise RoomSpecError("cannot safely read room document without O_NONBLOCK")
    descriptor = -1
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0),
        )
        # Check the opened object; a pathname check can race with replacement.
        # Symlinks to ordinary files retain the CLI's existing behavior.
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RoomSpecError("room document must be a regular file")
        if info.st_size > MAX_INPUT_BYTES:
            raise RoomSpecError("room document exceeds byte limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            # Keep the bound even if the file grows after descriptor inspection.
            return stream.read(MAX_INPUT_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mojum-room-check",
        description=(
            "Strictly validate a typed point-room JSON draft offline; "
            "read-only, exact number tokens preserved."
        ),
    )
    parser.add_argument("path", type=Path, help="room JSON file to validate")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="human-readable report or machine-readable JSON (default: text)",
    )
    args = parser.parse_args(argv)

    try:
        raw = _read_room_file(args.path)
    except RoomSpecError as exc:
        report = RoomReport((str(exc),), (), None)
    except OSError as exc:
        report = RoomReport(("cannot read room document: %s" % (exc.strerror,),), (), None)
    else:
        if len(raw) > MAX_INPUT_BYTES:
            report = RoomReport(("room document exceeds byte limit",), (), None)
        else:
            report = validate_room_bytes(raw)

    if args.format == "json":
        sys.stdout.write(json.dumps(report.as_json(), indent=2, sort_keys=True) + "\n")
    else:
        text = _text_report(report) + "\n"
        encoding = getattr(sys.stdout, "encoding", None)
        if encoding is not None:
            text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        sys.stdout.write(text)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
