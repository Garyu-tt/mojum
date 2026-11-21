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
from decimal import Decimal, InvalidOperation
import json
import re
import sys
from pathlib import Path

REPORT_SCHEMA = "mojum.room-spec-report/1"
MAX_INPUT_BYTES = 256 * 1024
MAX_OBSTACLES = 4096
# Pairwise overlap analysis is bounded so a huge but legal document cannot
# turn validation into quadratic work on a CPU-first fixture.
MAX_OVERLAP_ANALYSIS_OBSTACLES = 256
REQUIRED_KEYS = frozenset(("width", "height", "obstacles", "start"))

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
    try:
        number = Decimal(value)
    except InvalidOperation:
        errors.append("%s is not a finite decimal" % (label,))
        return None
    if not number.is_finite():
        errors.append("%s is not a finite decimal" % (label,))
        return None
    # Parsing a finite Decimal does not guarantee bounded arithmetic. Very
    # large JSON exponents (e.g. 1e999999999) are valid tokens but can raise
    # decimal.Overflow when room areas are calculated. Reject magnitudes
    # outside a conservative finite range before any arithmetic, including
    # operands too tiny for a downstream float-based room fixture.
    if not -300 <= number.adjusted() <= 300:
        errors.append("%s exceeds supported numeric magnitude" % (label,))
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


def validate_room_document(document):
    """Validate a parsed room document. Returns a :class:`RoomReport`."""
    errors = []
    warnings = []

    if type(document) is not dict:
        return RoomReport(("room document root must be a JSON object",), (), None)

    keys = set(document)
    missing = sorted(REQUIRED_KEYS - keys)
    unexpected = sorted(keys - REQUIRED_KEYS)
    if missing:
        errors.append("missing room key(s): %s" % (", ".join(missing),))
    if unexpected:
        errors.append("unexpected room key(s): %s" % (", ".join(unexpected),))
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
        "start": [str(v) for v in start],
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
        with args.path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
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
        sys.stdout.write(_text_report(report) + "\n")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
