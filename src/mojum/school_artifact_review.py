"""Read-only structural comparison of PRIVATE school make/revise artifacts.

This deterministic format/integrity tool is not a quality grader or learner
model. It performs no file, network, journal, audio, rendering or device I/O.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET

from .school_activity import (
    MAX_ARTIFACT_BYTES,
    MAX_PULSES,
    MAX_STROKES,
    RHYTHM_SCHEMA,
    _check_private_fixture_run,
)

REVIEW_SCHEMA = "mojum.school-artifact-review/1"
SVG_NS = "http://www.w3.org/2000/svg"
_PATH = re.compile(r"M ([0-9]{1,2}) ([0-9]{1,2}) L ([0-9]{1,2}) ([0-9]{1,2})$")


class SchoolArtifactReviewError(ValueError):
    """Rejected or unsupported school artifact/run."""


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SchoolArtifactReviewError("duplicate artifact JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise SchoolArtifactReviewError("non-finite artifact JSON constant")


def _parse_svg(content: str) -> dict:
    try:
        raw = content.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SchoolArtifactReviewError("invalid SVG UTF-8") from exc
    if not raw or len(raw) > MAX_ARTIFACT_BYTES:
        raise SchoolArtifactReviewError("SVG byte budget exceeded")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SchoolArtifactReviewError("SVG declarations are not supported")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError, UnicodeError, RecursionError) as exc:
        raise SchoolArtifactReviewError("invalid SVG document") from exc

    if root.tag != f"{{{SVG_NS}}}svg" or root.attrib != {
        "viewBox": "0 0 64 64", "role": "img", "aria-label": "Synthetic drawing"
    }:
        raise SchoolArtifactReviewError("unsupported SVG root")
    children = list(root)
    if len(children) != 2 or root.text is not None:
        raise SchoolArtifactReviewError("unsupported SVG structure")
    rect, group = children
    if any(child.tail is not None or child.text is not None for child in children):
        raise SchoolArtifactReviewError("SVG text nodes are not supported")
    if rect.tag != f"{{{SVG_NS}}}rect" or rect.attrib != {
        "width": "64", "height": "64", "fill": "white"
    } or list(rect):
        raise SchoolArtifactReviewError("unsupported SVG background")
    if group.tag != f"{{{SVG_NS}}}g" or group.attrib != {
        "fill": "none", "stroke": "black", "stroke-width": "2",
        "stroke-linecap": "round"
    }:
        raise SchoolArtifactReviewError("unsupported SVG stroke group")
    paths = list(group)
    if not 1 <= len(paths) <= MAX_STROKES:
        raise SchoolArtifactReviewError("SVG stroke budget exceeded")

    length = 0
    for path in paths:
        if (path.tag != f"{{{SVG_NS}}}path" or set(path.attrib) != {"d"} or list(path)
                or path.text is not None or path.tail is not None):
            raise SchoolArtifactReviewError("unsupported SVG element")
        match = _PATH.fullmatch(path.attrib["d"])
        if match is None:
            raise SchoolArtifactReviewError("unsupported SVG path command")
        x1, y1, x2, y2 = (int(part) for part in match.groups())
        if any(coord > 63 for coord in (x1, y1, x2, y2)):
            raise SchoolArtifactReviewError("SVG coordinate out of range")
        length += abs(x2 - x1) + abs(y2 - y1)

    return {"stroke_count": len(paths), "manhattan_length_units": length}


def _parse_rhythm(content: str) -> dict:
    try:
        raw = content.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise SchoolArtifactReviewError("invalid rhythm UTF-8") from exc
    if not raw or len(raw) > MAX_ARTIFACT_BYTES:
        raise SchoolArtifactReviewError("rhythm byte budget exceeded")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except SchoolArtifactReviewError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SchoolArtifactReviewError("invalid rhythm JSON") from exc
    if type(value) is not dict or set(value) != {"schema", "grid_steps", "pulses"}:
        raise SchoolArtifactReviewError("invalid rhythm fields")
    pulses = value["pulses"]
    if (value["schema"] != RHYTHM_SCHEMA or type(value["grid_steps"]) is not int
            or value["grid_steps"] != 64 or type(pulses) is not list
            or not 1 <= len(pulses) <= MAX_PULSES
            or any(type(p) is not int or not 0 <= p < 64 for p in pulses)
            or pulses != sorted(set(pulses))):
        raise SchoolArtifactReviewError("invalid rhythm grid")
    gaps = [pulses[i + 1] - pulses[i] for i in range(len(pulses) - 1)]
    gaps.append(64 - pulses[-1] + pulses[0])
    return {"pulse_count": len(pulses), "cyclic_gap_steps": gaps}


def review_run(run: dict) -> dict:
    """Return a redacted PRIVATE structural report for one validated school run.

    The input must be an in-memory PRIVATE school-run/1 object from the
    existing fixture runner. The caller is responsible for consent and
    provenance; schema and local hashes do not authenticate the writer.
    """
    try:
        _check_private_fixture_run(run)
    except Exception as exc:
        # Normalize caller-defined comparison/hash failures at the trust boundary.
        raise SchoolArtifactReviewError("school run contract rejected") from exc

    # bool is a subclass of int, and True == 1.  The legacy fixture validator
    # compares event ticks by equality only, so reject non-native integer ticks
    # before any structural measurements are derived.
    if any(type(event["sim_tick"]) is not int for event in run["events"]):
        raise SchoolArtifactReviewError("school event ticks must be exact integers")

    activity_id = run["activity_id"]
    artifact_kind = "svg" if activity_id == "visual_arts.starter" else "rhythm"
    parser = _parse_svg if artifact_kind == "svg" else _parse_rhythm
    summaries = []
    parsed_payloads = []
    for item in run["artifacts"]:
        content = item["content"]
        try:
            raw = content.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SchoolArtifactReviewError("invalid artifact UTF-8") from exc
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise SchoolArtifactReviewError("artifact byte budget exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item["sha256"]:
            raise SchoolArtifactReviewError("artifact digest mismatch")
        metrics = parser(content)
        summaries.append({
            "phase": item["phase"],
            "sha256": digest,
            "media_type": item["media_type"],
            "metrics": metrics,
        })
        parsed_payloads.append(metrics)

    make, revise = summaries
    before, after = parsed_payloads
    if artifact_kind == "svg":
        comparison = {
            "changed": make["sha256"] != revise["sha256"],
            "added_strokes": max(0, after["stroke_count"] - before["stroke_count"]),
            "removed_strokes": max(0, before["stroke_count"] - after["stroke_count"]),
            "manhattan_length_delta": (
                after["manhattan_length_units"] - before["manhattan_length_units"]
            ),
        }
    else:
        before_pulses = set(json.loads(run["artifacts"][0]["content"])["pulses"])
        after_pulses = set(json.loads(run["artifacts"][1]["content"])["pulses"])
        comparison = {
            "changed": make["sha256"] != revise["sha256"],
            "added_pulses": len(after_pulses - before_pulses),
            "removed_pulses": len(before_pulses - after_pulses),
            "symmetric_difference": len(before_pulses ^ after_pulses),
            "pulse_count_delta": after["pulse_count"] - before["pulse_count"],
        }

    return {
        "schema": REVIEW_SCHEMA,
        "visibility": "PRIVATE",
        "review_kind": "STRUCTURAL_ONLY",
        "activity_kind": artifact_kind,
        "artifacts": summaries,
        "comparison": comparison,
        "limitations": [
            "No quality, talent, competence, mastery or learning score is produced.",
            "Structural change is not evidence of improvement or transfer.",
            "The report is derived from caller-supplied PRIVATE data and is not authenticated.",
        ],
    }
