"""Bounded, deterministic, offline creative-school fixture (not a learning brain).

This module has NO I/O in the lesson runner, no hardware/audio access and no
Noosphere writer. Results remain PRIVATE unless an explicit redacted projection
is requested. It intentionally never infers creativity or subjective experience.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CATALOG_SCHEMA = "mojum.school-catalog/1"
EVENT_SCHEMA = "mojum.school-event/1"
RUN_SCHEMA = "mojum.school-run/1"
ART_SCHEMA = "mojum.art-svg/1"
RHYTHM_SCHEMA = "mojum.rhythm-grid/1"
PHASES = ("observe", "experiment", "make", "revise", "reflect")
ACTIVITIES = {"visual_arts.starter": "art", "music.starter": "rhythm"}
MAX_CATALOG_BYTES = 131_072
MAX_SECTIONS = 64
MAX_STROKES = 32
MAX_PULSES = 32
MAX_ARTIFACT_BYTES = 16_384
MAX_SIM_TICK = 2**31 - len(PHASES)
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class SchoolError(ValueError):
    """Rejected activity, unsafe input or incompatible curriculum."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SchoolError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise SchoolError("nonfinite JSON constant")


def load_catalog(path):
    """Read a small UTF-8 JSON catalog with unknown schema/duplicate-key denial."""
    try:
        p = Path(path)
        with p.open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise SchoolError("catalog byte budget exceeded")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise SchoolError("invalid catalog document") from exc
    return validate_catalog(document)


def _validate_catalog_wire_budget(document):
    """Enforce the same bounded UTF-8 JSON contract on in-memory catalogs.

    Iterative encoding stops at the byte limit instead of allocating an
    unbounded serialized copy of caller-provided extra metadata.
    """
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False)
        size = 0
        fragments = []
        for fragment in encoder.iterencode(document):
            size += len(fragment.encode("utf-8", errors="strict"))
            if size > MAX_CATALOG_BYTES:
                raise SchoolError("catalog byte budget exceeded")
            # Buffer at most one valid wire-size catalog for a lossless check.
            fragments.append(fragment)
        if json.loads("".join(fragments)) != document:
            raise SchoolError("catalog cannot round-trip losslessly as JSON")
    except SchoolError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise SchoolError("invalid catalog JSON encoding") from exc


def _nonempty(value, *, limit=160):
    # Prevent unpaired surrogates and control characters from crossing the
    # event/catalog boundary as strings that cannot be encoded or audited.
    return (type(value) is str and 0 < len(value) <= limit
            and bool(value.strip())
            and not any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f
                        or 0xd800 <= ord(c) <= 0xdfff for c in value))


def validate_catalog(document):
    """Validate the R0.1 sections without redefining their PLANNED status."""
    if not isinstance(document, dict) or document.get("schema") != CATALOG_SCHEMA:
        raise SchoolError("unknown school catalog schema")
    _validate_catalog_wire_budget(document)
    if document.get("learning_loop") != list(PHASES):
        raise SchoolError("invalid learning loop")
    sections = document.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise SchoolError("invalid section budget")
    ids, starter_ids = set(), set()
    for section in sections:
        if not isinstance(section, dict):
            raise SchoolError("invalid section")
        sid = section.get("id")
        if not isinstance(sid, str) or not _ID.fullmatch(sid) or sid in ids:
            raise SchoolError("duplicate/invalid section id")
        ids.add(sid)
        if not _nonempty(section.get("title"), limit=128):
            raise SchoolError("invalid section title")
        if section.get("implementation_status") != "PLANNED":
            raise SchoolError("unexpected catalog implementation status")
        for key in ("skills", "artifact_types"):
            values = section.get(key)
            if not isinstance(values, list) or not 1 <= len(values) <= 32 or not all(_nonempty(v, limit=128) for v in values):
                raise SchoolError("invalid " + key)
        starter = section.get("starter_activity")
        if not isinstance(starter, dict) or starter.get("id") != sid + ".starter":
            raise SchoolError("invalid starter activity")
        aid = starter["id"]
        if aid in starter_ids or not all(_nonempty(starter.get(k), limit=512) for k in ("prompt", "observation")):
            raise SchoolError("duplicate/invalid starter activity")
        starter_ids.add(aid)
    for section in sections:
        refs = section.get("cross_subject_ids")
        if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_SECTIONS:
            raise SchoolError("invalid cross-subject references")
        if any(not isinstance(ref, str) for ref in refs) or len(refs) != len(set(refs)):
            raise SchoolError("duplicate/invalid cross-subject reference")
        if section["id"] in refs or not set(refs) <= ids:
            raise SchoolError("unknown/self cross-subject reference")
    return document


def _require_plain_dict(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise SchoolError("invalid phase payload")


def _note(value):
    if not _nonempty(value):
        raise SchoolError("invalid observation/reflection")
    return value


def _int_grid(value, upper):
    if type(value) is not int or not 0 <= value < upper:
        raise SchoolError("invalid grid coordinate")
    return value


def _artifact_art(payload):
    _require_plain_dict(payload, {"strokes"})
    strokes = payload["strokes"]
    if not isinstance(strokes, list) or not 1 <= len(strokes) <= MAX_STROKES:
        raise SchoolError("stroke budget exceeded")
    paths = []
    for stroke in strokes:
        _require_plain_dict(stroke, {"x1", "y1", "x2", "y2"})
        x1, y1, x2, y2 = (_int_grid(stroke[k], 64) for k in ("x1", "y1", "x2", "y2"))
        paths.append(f'<path d="M {x1} {y1} L {x2} {y2}"/>')
    output = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" '
              'role="img" aria-label="Synthetic drawing"><rect width="64" height="64" '
              'fill="white"/><g fill="none" stroke="black" stroke-width="2" '
              'stroke-linecap="round">' + ''.join(paths) + '</g></svg>')
    return "image/svg+xml", output


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _artifact_rhythm(payload):
    _require_plain_dict(payload, {"pulses"})
    pulses = payload["pulses"]
    if not isinstance(pulses, list) or not 1 <= len(pulses) <= MAX_PULSES:
        raise SchoolError("pulse budget exceeded")
    if any(type(n) is not int or n < 0 or n >= 64 for n in pulses):
        raise SchoolError("invalid pulse")
    if pulses != sorted(set(pulses)):
        raise SchoolError("pulses must be sorted and unique")
    return "application/json", _canonical_json({"schema": RHYTHM_SCHEMA, "grid_steps": 64,
                                                 "pulses": pulses})


def run_activity(catalog, activity_id, script, *, sim_tick=0, learner_ref="fixture-learner"):
    """Run five scripted steps; return immutable-by-convention PRIVATE data only.

    The caller keeps custody of returned objects. No persistence or public output
    happens here. A revision replaces the previous artifact, not an asserted skill.
    """
    validate_catalog(catalog)
    if type(activity_id) is not str or activity_id not in ACTIVITIES or not any(s["starter_activity"]["id"] == activity_id for s in catalog["sections"]):
        raise SchoolError("unknown/unimplemented activity")
    if type(sim_tick) is not int or not 0 <= sim_tick <= MAX_SIM_TICK:
        raise SchoolError("invalid virtual tick")
    if not isinstance(learner_ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", learner_ref):
        raise SchoolError("invalid learner reference")
    _require_plain_dict(script, PHASES)
    for phase in ("observe", "experiment", "reflect"):
        _note(script[phase])
    builder = _artifact_art if ACTIVITIES[activity_id] == "art" else _artifact_rhythm
    artifacts = []
    for phase in ("make", "revise"):
        media_type, content = builder(script[phase])
        raw = content.encode("utf-8")
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise SchoolError("artifact byte budget exceeded")
        artifacts.append({"schema": ART_SCHEMA if media_type == "image/svg+xml" else RHYTHM_SCHEMA,
                          "phase": phase, "media_type": media_type, "visibility": "PRIVATE",
                          "sim_tick": sim_tick + PHASES.index(phase),
                          "sha256": hashlib.sha256(raw).hexdigest(), "content": content,
                          "provenance": "scripted-stdlib-fixture"})
    by_phase = {artifact["phase"]: artifact for artifact in artifacts}
    events = []
    for offset, phase in enumerate(PHASES):
        events.append({"schema": EVENT_SCHEMA, "visibility": "PRIVATE",
                       "learner_ref": learner_ref, "section_id": activity_id.split(".")[0],
                       "activity_id": activity_id, "phase": phase,
                       "sim_tick": sim_tick + offset,
                       "observation": script[phase] if phase not in by_phase else "artifact submitted",
                       "artifact_sha256": by_phase[phase]["sha256"] if phase in by_phase else None,
                       "provenance": "scripted-stdlib-fixture"})
    return {"schema": RUN_SCHEMA, "activity_id": activity_id,
            "learner_ref": learner_ref, "visibility": "PRIVATE",
            "sim_tick": sim_tick, "events": events, "artifacts": artifacts,
            "feedback": {"schema": "mojum.school-feedback/1", "visibility": "PRIVATE",
                         "revision_changed": artifacts[0]["sha256"] != artifacts[1]["sha256"],
                         "reviewed_phases": list(PHASES),
                         "note": "Scripted attempts only; no evidence of learning or transfer."}}


def _check_private_fixture_run(run):
    """Fail closed before deriving public metrics from an untrusted PRIVATE dict.

    Structural and local SHA checks are NOT an authenticity or authorization
    mechanism. A future Noosphere adapter must verify writer and consent itself.
    """
    if (type(run) is not dict or set(run) != {"schema", "activity_id", "learner_ref", "visibility", "sim_tick", "events", "artifacts", "feedback"}
            or run.get("schema") != RUN_SCHEMA or run.get("visibility") != "PRIVATE"):
        raise SchoolError("invalid private run envelope")
    aid, ref, tick = run.get("activity_id"), run.get("learner_ref"), run.get("sim_tick")
    if type(aid) is not str or aid not in ACTIVITIES:
        raise SchoolError("invalid private run activity")
    if type(ref) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", ref):
        raise SchoolError("invalid private run learner reference")
    if type(tick) is not int or not 0 <= tick <= MAX_SIM_TICK:
        raise SchoolError("invalid private run tick")
    artifacts, events = run.get("artifacts"), run.get("events")
    if type(artifacts) is not list or len(artifacts) != 2 or type(events) is not list or len(events) != len(PHASES):
        raise SchoolError("invalid private run collection")
    kind = ACTIVITIES[aid]
    expected_schema = ART_SCHEMA if kind == "art" else RHYTHM_SCHEMA
    expected_media = "image/svg+xml" if kind == "art" else "application/json"
    for index, phase in enumerate(("make", "revise")):
        item = artifacts[index]
        if (type(item) is not dict or set(item) != {"schema", "phase", "media_type", "visibility",
                                                  "sim_tick", "sha256", "content", "provenance"}):
            raise SchoolError("invalid private artifact fields")
        if any((
            item.get("schema") != expected_schema,
            item.get("phase") != phase,
            item.get("media_type") != expected_media,
            item.get("visibility") != "PRIVATE",
            item.get("sim_tick") != tick + PHASES.index(phase),
            item.get("provenance") != "scripted-stdlib-fixture",
        )):
            raise SchoolError("malformed private artifact metadata")
        content = item.get("content")
        if type(content) is not str:
            raise SchoolError("invalid private artifact content")
        try:
            payload = content.encode("utf-8")
        except UnicodeError as exc:
            raise SchoolError("invalid artifact UTF-8") from exc
        if len(payload) > MAX_ARTIFACT_BYTES or item.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise SchoolError("invalid private artifact hash or size")
    for offset, phase in enumerate(PHASES):
        event = events[offset]
        if (type(event) is not dict or set(event) != {"schema", "visibility", "learner_ref",
                                                     "section_id", "activity_id", "phase",
                                                     "sim_tick", "observation", "artifact_sha256",
                                                     "provenance"}):
            raise SchoolError("invalid private event fields")
        expected_hash = (artifacts[0]["sha256"] if phase == "make" else
                         artifacts[1]["sha256"] if phase == "revise" else None)
        if type(event) is not dict or any((
            event.get("schema") != EVENT_SCHEMA,
            event.get("visibility") != "PRIVATE",
            event.get("learner_ref") != ref,
            event.get("activity_id") != aid,
            event.get("section_id") != aid.split(".")[0],
            event.get("phase") != phase,
            event.get("sim_tick") != tick + offset,
            event.get("artifact_sha256") != expected_hash,
            event.get("provenance") != "scripted-stdlib-fixture",
        )):
            raise SchoolError("invalid private event chain")
        observation = event["observation"]
        if phase in ("make", "revise"):
            if observation != "artifact submitted":
                raise SchoolError("invalid artifact event observation")
        elif not _nonempty(observation):
            raise SchoolError("invalid private event observation")
        try:
            observation.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SchoolError("invalid observation UTF-8") from exc
    feedback = run["feedback"]
    if (type(feedback) is not dict or set(feedback) != {"schema", "visibility",
                "revision_changed", "reviewed_phases", "note"}
            or feedback["schema"] != "mojum.school-feedback/1"
            or feedback["visibility"] != "PRIVATE"
            or type(feedback["revision_changed"]) is not bool
            or feedback["revision_changed"] != (artifacts[0]["sha256"] != artifacts[1]["sha256"])
            or feedback["reviewed_phases"] != list(PHASES)
            or feedback["note"] != "Scripted attempts only; no evidence of learning or transfer."):
        raise SchoolError("invalid private feedback envelope")


def compare_trials(baseline, repeat):
    """Contrast distinct trials without grading or pretending to measure transfer."""
    _check_private_fixture_run(baseline)
    _check_private_fixture_run(repeat)
    if baseline["activity_id"] != repeat["activity_id"] or baseline["learner_ref"] != repeat["learner_ref"]:
        raise SchoolError("trials must target the same activity and learner")
    if type(baseline["sim_tick"]) is not int or type(repeat["sim_tick"]) is not int or repeat["sim_tick"] <= baseline["sim_tick"] + 4:
        raise SchoolError("repeat trial must follow baseline")
    return {"schema": "mojum.school-comparison/1", "visibility": "PRIVATE",
            "baseline_tick": baseline["sim_tick"], "repeat_tick": repeat["sim_tick"],
            "revision_changed": repeat["artifacts"][0]["sha256"] != repeat["artifacts"][1]["sha256"],
            "difference_from_baseline": baseline["artifacts"][1]["sha256"] != repeat["artifacts"][1]["sha256"],
            "transfer_measured": False, "learning_claimed": False}


def redacted_summary(run):
    """Only safe aggregate counts; do not export learner IDs, content or hashes."""
    _check_private_fixture_run(run)
    return {"schema": "mojum.school-public-summary/1", "visibility": "PUBLIC_REDACTED",
            "activity_id": run["activity_id"], "phase_count": len(run["events"]),
            "artifact_count": len(run["artifacts"]), "learning_claimed": False}
