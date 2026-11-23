"""Pure, opt-in, offline school lesson *proposals*, never lesson execution.

Consumes a verified synthetic catalog, not a learner, private journal, device,
scheduler, or external API. Identifiers and tick ranges are the only lesson
content exported; catalog prompts and observations are deliberately omitted.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .school_activity import (
    MAX_CATALOG_BYTES, PHASES, SchoolError, validate_catalog,
)

PLAN_SCHEMA = "mojum.school-lesson-plan/1"
PROVENANCE = "OFFLINE_SYNTHETIC_CATALOG_PROPOSAL"
MAX_LESSONS = 16
MAX_PHASE_TICKS = 256
MAX_TICK = (1 << 63) - 1


class LessonPlanError(ValueError):
    """Refused a non-voluntary, malformed or unbounded school plan."""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def plan_voluntary_lessons(
    catalog: dict[str, Any],
    section_ids: list[str],
    *,
    consent: bool,
    start_tick: int = 0,
    phase_ticks: int = 1,
    require_cross_subject_links: bool = False,
) -> dict[str, Any]:
    """Build a deterministic virtual-tick itinerary, with no side effects.

    Every requested section is visited exactly once and in the caller's order.
    When links are required, each consecutive pair must be an explicitly
    directed cross_subject_ids edge in the supplied catalog.

    A returned plan is NOT permission to execute a lesson. The consuming
    runtime must independently obtain current, revocable consent, revalidate
    state and check its own tick/episode limits.
    """
    if type(consent) is not bool or not consent:
        raise LessonPlanError("explicit voluntary opt-in is required")
    if type(require_cross_subject_links) is not bool:
        raise LessonPlanError("cross-subject mode must be boolean")
    if type(start_tick) is not int or not 0 <= start_tick <= MAX_TICK:
        raise LessonPlanError("invalid virtual start tick")
    if type(phase_ticks) is not int or not 1 <= phase_ticks <= MAX_PHASE_TICKS:
        raise LessonPlanError("invalid phase duration")
    if type(section_ids) is not list or not 1 <= len(section_ids) <= MAX_LESSONS:
        raise LessonPlanError("invalid lesson count")
    if any(type(section_id) is not str for section_id in section_ids):
        raise LessonPlanError("section IDs must be plain strings")
    # Catalog IDs follow school_activity._ID (at most 48 characters).
    # Refuse long caller-owned strings before hashing or dictionary lookup.
    if any(len(section_id) > 48 for section_id in section_ids):
        raise LessonPlanError("section ID exceeds catalog identifier bound")
    if len(set(section_ids)) != len(section_ids):
        raise LessonPlanError("duplicate section ID")
    if type(catalog) is not dict:
        raise LessonPlanError("catalog must be a plain object")

    # Validate the public catalog before serializing any attacker-owned
    # metadata. Snapshot to plain JSON containers to prevent later caller
    # mutation from changing the planned identifiers or directed links.
    try:
        validate_catalog(catalog)
        catalog_bytes = _json_bytes(catalog)
        if len(catalog_bytes) > MAX_CATALOG_BYTES:
            raise LessonPlanError("catalog byte budget exceeded")
        frozen_catalog = json.loads(catalog_bytes)
        validate_catalog(frozen_catalog)
    except (SchoolError, TypeError, ValueError, UnicodeError,
            OverflowError, RecursionError) as exc:
        raise LessonPlanError("invalid school catalog") from exc

    indexed = {section["id"]: section for section in frozen_catalog["sections"]}
    if any(section_id not in indexed for section_id in section_ids):
        raise LessonPlanError("unknown school section")
    if require_cross_subject_links:
        for source, target in zip(section_ids, section_ids[1:]):
            if target not in indexed[source]["cross_subject_ids"]:
                raise LessonPlanError("missing directed cross-subject link")

    # Compute the final tick before constructing any entries. These bounded
    # integers never depend on wall-clock time or external runtime state.
    tick_count = len(section_ids) * len(PHASES) * phase_ticks
    if tick_count - 1 > MAX_TICK - start_tick:
        raise LessonPlanError("virtual tick range exceeds signed-63-bit bound")

    lessons: list[dict[str, Any]] = []
    tick = start_tick
    for section_id in section_ids:
        phases: list[dict[str, int | str]] = []
        for phase in PHASES:
            phases.append({
                "phase": phase,
                "start_tick": tick,
                "end_tick": tick + phase_ticks - 1,
            })
            tick += phase_ticks
        lessons.append({
            "section_id": section_id,
            "activity_id": indexed[section_id]["starter_activity"]["id"],
            "start_tick": phases[0]["start_tick"],
            "end_tick": phases[-1]["end_tick"],
            "phases": phases,
        })

    plan = {
        "schema": PLAN_SCHEMA,
        "provenance": PROVENANCE,
        "consent": True,
        "executed": False,
        "physical_actuation": False,
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "require_cross_subject_links": require_cross_subject_links,
        "start_tick": start_tick,
        "phase_ticks": phase_ticks,
        "lessons": lessons,
    }
    # The digest is for deterministic comparison, not a signature or proof
    # that consent remains in force.
    return {**plan, "digest_sha256": hashlib.sha256(_json_bytes(plan)).hexdigest()}
