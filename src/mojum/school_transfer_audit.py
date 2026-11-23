"""Read-only, bounded audit of school-catalog cross-subject and lesson-transfer links.

The audit reports structural consistency only. It does not measure learning or
claim that a declared transfer has been demonstrated.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

SCHEMA = "mojum.school-catalog/1"
REPORT_SCHEMA = "mojum.school-transfer-audit/1"
MAX_BYTES = 65_536
MAX_SECTIONS = 32
MAX_LESSONS_PER_SECTION = 64
MAX_LINKS = 128
MAX_TREE_NODES = 20_000
MAX_DEPTH = 24


class SchoolTransferAuditError(ValueError):
    """Input is malformed, ambiguous, or outside the audit resource budget."""


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    source: str
    target: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "source": self.source,
                "target": self.target, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class AuditReport:
    schema: str
    catalog_sha256: str
    section_count: int
    lesson_count: int
    cross_link_count: int
    lesson_transfer_count: int
    findings: tuple[Finding, ...]
    report_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "catalog_sha256": self.catalog_sha256,
            "section_count": self.section_count,
            "lesson_count": self.lesson_count,
            "cross_link_count": self.cross_link_count,
            "lesson_transfer_count": self.lesson_transfer_count,
            "findings": [item.as_dict() for item in self.findings],
            "report_sha256": self.report_sha256,
            "interpretation": "STRUCTURAL_CATALOG_AUDIT_ONLY_NOT_LEARNING_EVIDENCE",
        }


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchoolTransferAuditError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _check_native_json(value: Any) -> None:
    """Reject non-JSON Python objects, aliases/cycles, and adversarial subclasses."""
    seen: set[int] = set()
    nodes = 0
    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            seen.remove(id(current))
            continue
        nodes += 1
        if nodes > MAX_TREE_NODES or depth > MAX_DEPTH:
            raise SchoolTransferAuditError("catalog tree exceeds structural budget")
        kind = type(current)
        if kind is dict:
            ident = id(current)
            if ident in seen:
                raise SchoolTransferAuditError("catalog contains a cycle or shared container")
            seen.add(ident)
            stack.append((current, depth, True))
            for key, child in current.items():
                if type(key) is not str:
                    raise SchoolTransferAuditError("JSON object keys must be exact strings")
                stack.append((child, depth + 1, False))
        elif kind is list:
            ident = id(current)
            if ident in seen:
                raise SchoolTransferAuditError("catalog contains a cycle or shared container")
            seen.add(ident)
            stack.append((current, depth, True))
            for child in current:
                stack.append((child, depth + 1, False))
        elif kind in (str, bool, int) or current is None:
            if kind is str and len(current) > 4096:
                raise SchoolTransferAuditError("catalog string exceeds limit")
        elif kind is float:
            if not math.isfinite(current):
                raise SchoolTransferAuditError("catalog contains a non-finite number")
        else:
            raise SchoolTransferAuditError("catalog contains a non-native JSON value")


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        payload = (encoded + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as error:
        raise SchoolTransferAuditError("catalog cannot be canonically encoded") from error
    if len(payload) > MAX_BYTES:
        raise SchoolTransferAuditError("catalog exceeds byte limit")
    return payload


def _load(value: Any) -> tuple[dict[str, Any], bytes]:
    if type(value) is bytes:
        if len(value) > MAX_BYTES:
            raise SchoolTransferAuditError("catalog exceeds byte limit")
        try:
            catalog = json.loads(value.decode("utf-8"),
                                 object_pairs_hook=_no_duplicate_pairs,
                                 parse_constant=lambda _: (_ for _ in ()).throw(
                                     SchoolTransferAuditError("non-finite JSON number")))
        except SchoolTransferAuditError:
            raise
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise SchoolTransferAuditError("catalog is not valid bounded UTF-8 JSON") from error
    else:
        catalog = value
    if type(catalog) is not dict:
        raise SchoolTransferAuditError("catalog must be an exact JSON object")
    _check_native_json(catalog)
    payload = _canonical(catalog)
    if catalog.get("schema") != SCHEMA:
        raise SchoolTransferAuditError("unsupported school catalog schema")
    return catalog, payload


def audit_catalog(value: Any) -> AuditReport:
    """Audit a native catalog mapping or UTF-8 JSON bytes without side effects."""
    catalog, payload = _load(value)
    sections = catalog.get("sections")
    if type(sections) is not list or not 1 <= len(sections) <= MAX_SECTIONS:
        raise SchoolTransferAuditError("invalid section list")

    ids: list[str] = []
    section_links: dict[str, list[str]] = {}
    lessons: list[tuple[str, str, list[str]]] = []
    findings: list[Finding] = []
    lesson_ids: set[str] = set()
    cross_count = 0
    transfer_count = 0

    for section in sections:
        if type(section) is not dict:
            raise SchoolTransferAuditError("section must be an exact object")
        sid = section.get("id")
        if type(sid) is not str or not sid or len(sid) > 48 or sid in ids:
            raise SchoolTransferAuditError("invalid or duplicate section ID")
        ids.append(sid)
        links = section.get("cross_subject_ids", [])
        if type(links) is not list or len(links) > MAX_LINKS:
            raise SchoolTransferAuditError(f"invalid cross_subject_ids for {sid}")
        if any(type(target) is not str or not target or len(target) > 48
               for target in links):
            raise SchoolTransferAuditError(f"invalid cross-subject target for {sid}")
        section_links[sid] = list(links)
        cross_count += len(links)
        lessons_value = section.get("lessons", [])
        if type(lessons_value) is not list or len(lessons_value) > MAX_LESSONS_PER_SECTION:
            raise SchoolTransferAuditError(f"invalid lessons list for {sid}")
        for lesson in lessons_value:
            if type(lesson) is not dict:
                raise SchoolTransferAuditError(f"lesson in {sid} must be an exact object")
            lid = lesson.get("id")
            if type(lid) is not str or not lid or len(lid) > 96:
                raise SchoolTransferAuditError(f"invalid lesson ID in {sid}")
            if lid in lesson_ids:
                findings.append(Finding("DUPLICATE_LESSON_ID", sid, lid,
                                        "lesson IDs must be unique across the catalog"))
            lesson_ids.add(lid)
            transfers = lesson.get("transfer_to", [])
            if type(transfers) is not list or len(transfers) > MAX_LINKS:
                raise SchoolTransferAuditError(f"invalid transfer_to for {lid}")
            if any(type(target) is not str or not target or len(target) > 48
                   for target in transfers):
                raise SchoolTransferAuditError(f"invalid transfer target for {lid}")
            transfer_count += len(transfers)
            lessons.append((sid, lid, list(transfers)))

    known = set(ids)
    for source, targets in section_links.items():
        if len(targets) != len(set(targets)):
            findings.append(Finding("DUPLICATE_CROSS_SUBJECT_LINK", source, "",
                                    "subject repeats a cross_subject_ids target"))
        for target in targets:
            if target == source:
                findings.append(Finding("SELF_CROSS_SUBJECT_LINK", source, target,
                                        "subject links to itself"))
            elif target not in known:
                findings.append(Finding("UNKNOWN_CROSS_SUBJECT_TARGET", source, target,
                                        "target subject does not exist"))
            elif source not in section_links.get(target, []):
                findings.append(Finding("MISSING_REVERSE_CROSS_SUBJECT_LINK", source,
                                        target, "subject link is not reciprocated"))
    for source, lesson_id, targets in lessons:
        if len(targets) != len(set(targets)):
            findings.append(Finding("DUPLICATE_LESSON_TRANSFER", lesson_id, "",
                                    "lesson repeats a transfer_to target"))
        for target in targets:
            if target == source:
                findings.append(Finding("SELF_LESSON_TRANSFER", lesson_id, target,
                                        "lesson transfers to its own subject"))
            elif target not in known:
                findings.append(Finding("UNKNOWN_LESSON_TRANSFER_TARGET", lesson_id,
                                        target, "target subject does not exist"))

    findings.sort(key=lambda item: (item.code, item.source, item.target, item.detail))
    digest_input = {
        "schema": REPORT_SCHEMA,
        "catalog_sha256": hashlib.sha256(payload).hexdigest(),
        "section_count": len(ids),
        "lesson_count": len(lessons),
        "cross_link_count": cross_count,
        "lesson_transfer_count": transfer_count,
        "findings": [item.as_dict() for item in findings],
    }
    report_digest = hashlib.sha256(_canonical(digest_input)).hexdigest()
    return AuditReport(
        schema=REPORT_SCHEMA,
        catalog_sha256=hashlib.sha256(payload).hexdigest(),
        section_count=len(ids),
        lesson_count=len(lessons),
        cross_link_count=cross_count,
        lesson_transfer_count=transfer_count,
        findings=tuple(findings),
        report_sha256=report_digest,
    )


__all__ = ["AuditReport", "Finding", "SchoolTransferAuditError", "audit_catalog"]
