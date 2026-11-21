"""Bounded, deterministic PRIVATE synthetic creative-school fixtures.

Not a learner, teacher, Noosphere writer, or physical actuator.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

CATALOG_SCHEMA = "mojum.school-catalog/1"
EVENT_SCHEMA = "mojum.school-scripted-event/1"
RUN_SCHEMA = "mojum.school-scripted-run/1"
PHASES = ("observe", "experiment", "make", "revise", "reflect")
MAX_CATALOG_BYTES = 65_536
MAX_SECTIONS = 32
MAX_ARTIFACT_BYTES = 32_768
MAX_TICK = 2**63 - 1
_ID = re.compile(r"[a-z][a-z0-9_]{0,47}\Z", re.ASCII)
_SUPPORTED = {"visual_arts": "visual_arts.starter", "music": "music.starter"}


class SchoolContractError(ValueError):
    """The catalog or synthetic activity violates the bounded fixture contract."""


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchoolContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


def _validated_catalog_bytes(catalog: Any) -> bytes:
    if not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA:
        raise SchoolContractError("unsupported school catalog schema")
    if catalog.get("learning_loop") != list(PHASES):
        raise SchoolContractError("unsupported school phase order")
    sections = catalog.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        raise SchoolContractError("invalid school section count")
    section_ids: set[str] = set()
    activity_ids: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            raise SchoolContractError("invalid section object")
        sid = section.get("id")
        if not isinstance(sid, str) or _ID.fullmatch(sid) is None or sid in section_ids:
            raise SchoolContractError("invalid/duplicate section ID")
        section_ids.add(sid)
        if not _nonempty_text(section.get("title")):
            raise SchoolContractError("missing section title")
        for key in ("skills", "artifact_types"):
            values = section.get(key)
            if not isinstance(values, list) or not 1 <= len(values) <= 32 or not all(_nonempty_text(v) for v in values):
                raise SchoolContractError(f"invalid {key}")
        references = section.get("cross_subject_ids")
        if not isinstance(references, list) or not 1 <= len(references) <= MAX_SECTIONS:
            raise SchoolContractError("invalid cross-subject references")
        if any(not isinstance(x, str) or _ID.fullmatch(x) is None for x in references):
            raise SchoolContractError("invalid cross-subject ID")
        if len(references) != len(set(references)) or sid in references:
            raise SchoolContractError("duplicate or self cross-subject link")
        starter = section.get("starter_activity")
        if not isinstance(starter, dict):
            raise SchoolContractError("missing starter activity")
        aid = starter.get("id")
        if aid != f"{sid}.starter" or aid in activity_ids:
            raise SchoolContractError("invalid/duplicate activity ID")
        activity_ids.add(aid)
        if not _nonempty_text(starter.get("prompt")) or not _nonempty_text(starter.get("observation")):
            raise SchoolContractError("invalid starter prompt/observation")
        if section.get("implementation_status") != "PLANNED":
            raise SchoolContractError("unexpected proposed implementation status")
    for section in sections:
        if not set(section["cross_subject_ids"]).issubset(section_ids):
            raise SchoolContractError("unresolved cross-subject reference")

    try:
        payload = _canonical_json(catalog)
    except (TypeError, ValueError, RecursionError) as error:
        raise SchoolContractError("school catalog cannot be encoded as UTF-8 JSON") from error
    if len(payload) > MAX_CATALOG_BYTES:  # Include the canonical trailing newline in the limit.
        raise SchoolContractError("school catalog exceeds byte limit")
    return payload


def validate_catalog(catalog: Any) -> dict[str, Any]:
    """Validate the R0.1 schema and its bounded, canonical JSON representation."""
    _validated_catalog_bytes(catalog)
    return catalog


def load_catalog(path: str | Path) -> dict[str, Any]:
    """Bound file bytes and reject duplicate JSON keys before validation."""
    with open(path, "rb") as source:
        payload = source.read(MAX_CATALOG_BYTES + 1)
    if len(payload) > MAX_CATALOG_BYTES:
        raise SchoolContractError("school catalog exceeds byte limit")
    try:
        catalog = json.loads(payload.decode("utf-8"), object_pairs_hook=_object_no_duplicates)
    except SchoolContractError:
        raise
    except (ValueError, RecursionError) as error:
        raise SchoolContractError("school catalog is not valid UTF-8 JSON") from error
    return validate_catalog(catalog)


def _canonical_json(value: Any) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return (encoded + "\n").encode("utf-8")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _paint() -> tuple[bytes, bytes, int, int]:
    """24x24 P3 art; scripted revision centers a bilateral motif."""
    def render(center: float) -> tuple[bytes, int]:
        width = height = 24
        background = (246, 248, 250)
        pigment = (86, 139, 193)
        highlight = (245, 186, 103)
        core = (52, 85, 122)
        pixels = []
        for y in range(height):
            for x in range(width):
                dx, dy = x - center, y - 11.5
                radius = dx * dx + dy * dy
                if radius > 64:
                    color = background
                elif radius < 9:
                    color = core
                elif dx * dx < 4 or dy * dy < 4:
                    color = highlight
                else:
                    color = pigment
                pixels.append(color)
        symmetric_pairs = sum(
            pixels[y * width + x] == pixels[y * width + width - 1 - x]
            for y in range(height) for x in range(width // 2)
        )
        lines = ["P3", "# mojum synthetic visual art; no personal data", f"{width} {height}", "255"]
        lines.extend(" ".join(map(str, color)) for color in pixels)
        return ("\n".join(lines) + "\n").encode("ascii"), symmetric_pairs

    first, before = render(9.5)
    revised, after = render(11.5)
    return first, revised, before, after


def _rhythm() -> tuple[bytes, bytes, int, int]:
    target = [0, 500, 1000, 1500]
    first = [0, 430, 1030, 1600]
    second = target[:]
    def encode(beats: list[int]) -> bytes:
        return _canonical_json({
            "schema": "mojum.rhythm-timing/1", "units": "milliseconds",
            "target_beats_ms": target, "played_beats_ms": beats,
            "duration_ms": 2000, "origin": "SCRIPTED_FIXTURE_ONLY",
        })
    def score(beats: list[int]) -> int:
        return sum(abs(a - b) for a, b in zip(target, beats))
    return encode(first), encode(second), score(first), score(second)


def run_scripted_activity(
    catalog: dict[str, Any], section_id: str, *, start_tick: int = 0,
    mode: str = "revision",
) -> dict[str, Any]:
    """Return deterministic PRIVATE events and artifacts without side effects.

    revision: scripted changed work. frozen_control: independent no-change control.
    Neither mode models a genuine learner or transferable skill.
    """
    catalog_bytes = _validated_catalog_bytes(catalog)
    if not isinstance(section_id, str) or section_id not in _SUPPORTED:
        raise SchoolContractError("section is not supported by synthetic R0.2 runner")
    if not any(s["id"] == section_id and s["starter_activity"]["id"] == _SUPPORTED[section_id]
               for s in catalog["sections"]):
        raise SchoolContractError("requested starter activity is absent")
    if type(start_tick) is not int or not 0 <= start_tick <= MAX_TICK - 4:
        raise SchoolContractError("invalid virtual tick")
    if mode not in ("revision", "frozen_control"):
        raise SchoolContractError("unsupported school mode")
    if section_id == "visual_arts":
        baseline, changed, score_before, score_changed = _paint()
        metric, better_direction, suffix = "mirror_equal_pairs_of_288", "higher", "ppm"
    else:
        baseline, changed, score_before, score_changed = _rhythm()
        metric, better_direction, suffix = "sum_absolute_timing_error_ms", "lower", "json"
    revised = changed if mode == "revision" else baseline
    score_after = score_changed if mode == "revision" else score_before
    artifacts = {f"baseline.{suffix}": baseline, f"revised.{suffix}": revised}
    if any(len(payload) > MAX_ARTIFACT_BYTES for payload in artifacts.values()):
        raise SchoolContractError("synthetic artifact exceeds byte budget")
    catalog_hash = _digest(catalog_bytes)

    def artifact_meta(filename: str) -> dict[str, Any]:
        data = artifacts[filename]
        return {"filename": filename, "sha256": _digest(data), "size_bytes": len(data)}

    baseline_meta = artifact_meta(f"baseline.{suffix}")
    revised_meta = artifact_meta(f"revised.{suffix}")
    provenance = {
        "runtime": "mojum.school-scripted-r02/1", "kind": "SCRIPTED_FIXTURE",
        "catalog_sha256": catalog_hash, "mode": mode, "physical_actuation": False,
    }
    evidence = [
        {"stimulus": "fixed public synthetic template"},
        {"control": "fixed scripted edits, no agent policy"},
        {"artifact": baseline_meta, "metric": metric, "measured": score_before},
        {"artifact": revised_meta, "metric": metric, "measured": score_after},
        {"metric": metric, "baseline": score_before, "revised": score_after,
         "delta": score_after - score_before, "better_direction": better_direction,
         "inference": "SCRIPTED_DEMO_NOT_LEARNING"},
    ]
    events = [
        {"schema": EVENT_SCHEMA, "phase": phase, "sim_tick": start_tick + i,
         "section_id": section_id, "activity_id": _SUPPORTED[section_id],
         "learner_ref": "anonymous-scripted-fixture", "visibility": "PRIVATE",
         "provenance": provenance, "evidence": evidence[i]}
        for i, phase in enumerate(PHASES)
    ]
    return {"schema": RUN_SCHEMA, "section_id": section_id, "mode": mode,
            "visibility": "PRIVATE", "events": events, "artifacts": artifacts,
            "feedback": evidence[-1]}


def _verify_scripted_export(run: Any) -> None:
    """Fail closed before touching disk if a caller altered fixture evidence.

    This checks the deterministic SCRIPTED_FIXTURE contract, not who supplied
    the catalog digest. A SHA-256 checksum is not a signature or consent proof.
    """
    keys = {"schema", "section_id", "mode", "visibility", "events",
            "artifacts", "feedback"}
    if type(run) is not dict or set(run) != keys:
        raise SchoolContractError("invalid scripted run envelope")
    section, mode = run["section_id"], run["mode"]
    if (run["schema"] != RUN_SCHEMA or run["visibility"] != "PRIVATE"
            or type(section) is not str or section not in _SUPPORTED
            or type(mode) is not str or mode not in ("revision", "frozen_control")):
        raise SchoolContractError("unsupported scripted run identity")

    events, artifacts = run["events"], run["artifacts"]
    if (type(events) is not list or len(events) != len(PHASES)
            or type(events[0]) is not dict):
        raise SchoolContractError("invalid scripted event sequence")
    first_tick = events[0].get("sim_tick")
    if type(first_tick) is not int or not 0 <= first_tick <= MAX_TICK - 4:
        raise SchoolContractError("invalid scripted event tick")
    provenance = events[0].get("provenance")
    if (type(provenance) is not dict
            or set(provenance) != {"runtime", "kind", "catalog_sha256",
                                  "mode", "physical_actuation"}
            or provenance["runtime"] != "mojum.school-scripted-r02/1"
            or provenance["kind"] != "SCRIPTED_FIXTURE"
            or provenance["mode"] != mode
            or provenance["physical_actuation"] is not False
            or type(provenance["catalog_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", provenance["catalog_sha256"]) is None):
        raise SchoolContractError("invalid scripted provenance")

    if section == "visual_arts":
        baseline, revised, before, after = _paint()
        metric, direction, suffix = "mirror_equal_pairs_of_288", "higher", "ppm"
    else:
        baseline, revised, before, after = _rhythm()
        metric, direction, suffix = "sum_absolute_timing_error_ms", "lower", "json"
    if mode == "frozen_control":
        revised, after = baseline, before
    expected_artifacts = {f"baseline.{suffix}": baseline,
                          f"revised.{suffix}": revised}
    if (type(artifacts) is not dict
            or set(artifacts) != set(expected_artifacts)
            or any(type(value) is not bytes or len(value) > MAX_ARTIFACT_BYTES
                   for value in artifacts.values())
            or artifacts != expected_artifacts):
        raise SchoolContractError("scripted artifact differs from deterministic fixture")

    def artifact_meta(name: str) -> dict[str, Any]:
        data = expected_artifacts[name]
        return {"filename": name, "sha256": _digest(data), "size_bytes": len(data)}

    evidence = [
        {"stimulus": "fixed public synthetic template"},
        {"control": "fixed scripted edits, no agent policy"},
        {"artifact": artifact_meta(f"baseline.{suffix}"), "metric": metric,
         "measured": before},
        {"artifact": artifact_meta(f"revised.{suffix}"), "metric": metric,
         "measured": after},
        {"metric": metric, "baseline": before, "revised": after,
         "delta": after - before, "better_direction": direction,
         "inference": "SCRIPTED_DEMO_NOT_LEARNING"},
    ]
    expected_events = [
        {"schema": EVENT_SCHEMA, "phase": phase, "sim_tick": first_tick + index,
         "section_id": section, "activity_id": _SUPPORTED[section],
         "learner_ref": "anonymous-scripted-fixture", "visibility": "PRIVATE",
         "provenance": provenance, "evidence": evidence[index]}
        for index, phase in enumerate(PHASES)
    ]
    # Python dict/list equality treats True == 1 and 1.0 == 1. PRIVATE
    # event evidence must match its exact JSON wire types, not just values.
    try:
        actual_wire = _canonical_json({"events": events, "feedback": run["feedback"]})
        expected_wire = _canonical_json({"events": expected_events, "feedback": evidence[-1]})
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchoolContractError("invalid scripted evidence encoding") from exc
    if actual_wire != expected_wire:
        raise SchoolContractError("scripted events or feedback do not match artifacts")


def write_fixture(run: dict[str, Any], output_dir: str | Path) -> Path:
    """Write a new owner-private directory and roll back on a write error.

    No assertion of security against malicious same-UID filesystem races.
    """
    _verify_scripted_export(run)
    artifacts = list(run["artifacts"].items())
    for name, payload in artifacts:
        if name not in ("baseline.ppm", "revised.ppm", "baseline.json", "revised.json"):
            raise SchoolContractError("unsupported output filename")
        if not isinstance(payload, bytes) or len(payload) > MAX_ARTIFACT_BYTES:
            raise SchoolContractError("invalid output artifact bytes or size")
    manifest = {key: value for key, value in run.items() if key != "artifacts"}
    try:
        manifest_bytes = _canonical_json(manifest)
    except (TypeError, ValueError, RecursionError) as error:
        raise SchoolContractError("school output manifest cannot be encoded as UTF-8 JSON") from error
    files = sorted(artifacts) + [("events.json", manifest_bytes)]

    target = Path(output_dir)
    with ExitStack() as rollback:
        target.mkdir(mode=0o700, parents=False, exist_ok=False)
        rollback.callback(target.rmdir)
        for name, payload in files:
            path = target / name
            with path.open("xb") as stream:
                rollback.callback(path.unlink, missing_ok=True)
                os.chmod(path, 0o600)
                stream.write(payload)
        rollback.pop_all()
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRIVATE offline scripted art/rhythm fixture, not a learned agent")
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--section", required=True, choices=tuple(_SUPPORTED))
    parser.add_argument("--out", required=True, type=Path, help="brand new private output directory")
    parser.add_argument("--start-tick", type=int, default=0)
    parser.add_argument("--mode", choices=("revision", "frozen_control"), default="revision")
    args = parser.parse_args(argv)
    try:
        run = run_scripted_activity(load_catalog(args.catalog), args.section,
                                    start_tick=args.start_tick, mode=args.mode)
        write_fixture(run, args.out)
    except (SchoolContractError, FileExistsError, OSError) as error:
        parser.error(str(error))
    print(_canonical_json({"status": "SYNTHETIC_FIXTURE_ONLY", "visibility": "PRIVATE",
                           "mode": args.mode, "phase_count": len(run["events"])}).decode("utf-8").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
