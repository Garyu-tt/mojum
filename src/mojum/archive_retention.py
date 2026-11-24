"""Advisory retention planner for PUBLIC_SYNTHETIC sealed archive metadata.

This module never reads, writes, moves, or deletes archive data. It only plans
which already-described sealed segments may be presented for operator review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

SCHEMA = "mojum.archive-retention-input/1"
REPORT_SCHEMA = "mojum.archive-retention-plan/1"
MAX_SEGMENTS = 4096
MAX_FAILURE_DOMAINS = 64
MAX_ID_LENGTH = 128
_ZERO_HEAD = "0" * 64
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RetentionError(ValueError):
    """Invalid or unsafe retention-planning input."""


@dataclass(frozen=True)
class SegmentDecision:
    segment_id: str
    start_sequence: int
    end_sequence: int
    action: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RetentionPlan:
    schema: str
    restore_head: str
    restore_anchor_sequence: int
    decisions: tuple[SegmentDecision, ...]
    eligible_segment_ids: tuple[str, ...]
    digest: str


def _exact_int(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _identifier(value: object) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return type(value) is str and _HEX_64.fullmatch(value) is not None


def _fail(message: str) -> None:
    raise RetentionError(message)


def plan_retention(raw: dict) -> RetentionPlan:
    """Build a deterministic, non-destructive retention plan.

    Required top-level fields:
      schema, consent, current_tick, minimum_age_ticks,
      minimum_retained_segments, minimum_failure_domains,
      restore_anchor, restore_head, segments.

    Each segment has:
      segment_id, start_sequence, end_sequence, sealed_tick,
      content_sha256, previous_head_sha256, head_sha256,
      failure_domains, verified.

    The restore anchor is a caller-supplied trusted checkpoint boundary.
    This function validates internal consistency only; it does not authenticate
    that anchor, verify signatures, or inspect any archive bytes.
    """
    if type(raw) is not dict:
        _fail("input must be an exact dict")
    required = {
        "schema", "consent", "current_tick", "minimum_age_ticks",
        "minimum_retained_segments", "minimum_failure_domains",
        "restore_anchor", "restore_head", "segments",
    }
    if set(raw) != required:
        _fail("input fields do not match schema")
    if type(raw["schema"]) is not str or raw["schema"] != SCHEMA:
        _fail("unsupported schema")
    if type(raw["consent"]) is not str or raw["consent"] != "PUBLIC_SYNTHETIC":
        _fail("explicit PUBLIC_SYNTHETIC consent required")

    current_tick = raw["current_tick"]
    min_age = raw["minimum_age_ticks"]
    min_segments = raw["minimum_retained_segments"]
    min_domains = raw["minimum_failure_domains"]
    if not _exact_int(current_tick) or not _exact_int(min_age):
        _fail("tick values must be nonnegative exact integers")
    if not _exact_int(min_segments, minimum=1) or min_segments > MAX_SEGMENTS:
        _fail("minimum_retained_segments is outside bounds")
    if not _exact_int(min_domains, minimum=2) or min_domains > MAX_FAILURE_DOMAINS:
        _fail("minimum_failure_domains is outside bounds")

    anchor = raw["restore_anchor"]
    if type(anchor) is not dict or set(anchor) != {"sequence", "head_sha256"}:
        _fail("invalid restore anchor")
    anchor_seq, anchor_head = anchor["sequence"], anchor["head_sha256"]
    if not _exact_int(anchor_seq) or not _digest(anchor_head):
        _fail("invalid restore anchor values")
    restore_head = raw["restore_head"]
    if not _digest(restore_head):
        _fail("invalid restore head")

    segments = raw["segments"]
    if type(segments) is not list or len(segments) > MAX_SEGMENTS:
        _fail("segments must be a bounded exact list")
    if not segments:
        if anchor_seq != 0 or anchor_head != _ZERO_HEAD or restore_head != _ZERO_HEAD:
            _fail("empty archive must use the genesis anchor and head")
        return _build_plan(restore_head, anchor_seq, (), ())

    normalized = []
    seen_ids = set()
    previous_end = None
    previous_head = None
    for item in segments:
        fields = {
            "segment_id", "start_sequence", "end_sequence", "sealed_tick",
            "content_sha256", "previous_head_sha256", "head_sha256",
            "failure_domains", "verified",
        }
        if type(item) is not dict or set(item) != fields:
            _fail("invalid segment record")
        sid = item["segment_id"]
        start, end, sealed_tick = item["start_sequence"], item["end_sequence"], item["sealed_tick"]
        if not _identifier(sid) or sid in seen_ids:
            _fail("invalid or duplicate segment_id")
        seen_ids.add(sid)
        if not _exact_int(start, minimum=1) or not _exact_int(end, minimum=1) or end < start:
            _fail("invalid segment sequence interval")
        if not _exact_int(sealed_tick) or sealed_tick > current_tick:
            _fail("invalid or future sealed_tick")
        if not all(_digest(item[k]) for k in ("content_sha256", "previous_head_sha256", "head_sha256")):
            _fail("invalid segment digest")
        domains = item["failure_domains"]
        if type(domains) is not list or not 1 <= len(domains) <= MAX_FAILURE_DOMAINS:
            _fail("invalid failure_domains")
        if any(not _identifier(d) for d in domains) or len(set(domains)) != len(domains):
            _fail("invalid or duplicate failure domain")
        if type(item["verified"]) is not bool:
            _fail("verified must be an exact bool")
        if previous_end is not None and start != previous_end + 1:
            _fail("segments must form a contiguous sequence")
        if previous_head is not None and item["previous_head_sha256"] != previous_head:
            _fail("segment hash chain is discontinuous")
        normalized.append({
            "segment_id": sid, "start_sequence": start, "end_sequence": end,
            "sealed_tick": sealed_tick, "content_sha256": item["content_sha256"],
            "previous_head_sha256": item["previous_head_sha256"],
            "head_sha256": item["head_sha256"],
            "failure_domains": sorted(domains), "verified": item["verified"],
        })
        previous_end, previous_head = end, item["head_sha256"]

    # Anchor must be the genesis boundary or an exact segment boundary.
    anchor_matches = anchor_seq == 0 and anchor_head == normalized[0]["previous_head_sha256"]
    if anchor_seq > 0:
        anchor_matches = False
    for segment in normalized:
        if segment["end_sequence"] == anchor_seq and segment["head_sha256"] == anchor_head:
            anchor_matches = segment["verified"]
    if not anchor_matches:
        _fail("restore anchor is not a verified sequence boundary")
    if previous_head != restore_head:
        _fail("restore_head does not match the final segment head")

    # The full sequence must begin immediately after the declared genesis
    # boundary when no checkpoint anchor is supplied; with a checkpoint anchor,
    # earlier prefix records may be retained as history but not required for
    # replay from that checkpoint.
    if normalized[0]["start_sequence"] != 1:
        _fail("segment inventory must include its sequence origin")

    decisions = []
    eligible = []
    newest_ids = {s["segment_id"] for s in normalized[-min_segments:]}
    for segment in normalized:
        reasons = []
        if segment["end_sequence"] > anchor_seq:
            reasons.append("required_after_restore_anchor")
        if segment["segment_id"] in newest_ids:
            reasons.append("minimum_recent_segment_floor")
        if current_tick - segment["sealed_tick"] < min_age:
            reasons.append("minimum_age_not_reached")
        if len(segment["failure_domains"]) < min_domains:
            reasons.append("insufficient_failure_domains")
        if not segment["verified"]:
            reasons.append("unverified_segment")
        if reasons:
            action = "KEEP"
        else:
            action = "ELIGIBLE_FOR_OPERATOR_REVIEW"
            eligible.append(segment["segment_id"])
            reasons.append("all_advisory_retention_conditions_met")
        decisions.append(SegmentDecision(
            segment["segment_id"], segment["start_sequence"], segment["end_sequence"],
            action, tuple(reasons),
        ))

    return _build_plan(restore_head, anchor_seq, tuple(decisions), tuple(eligible))


def _build_plan(restore_head, anchor_seq, decisions, eligible):
    payload = {
        "schema": REPORT_SCHEMA,
        "restore_head": restore_head,
        "restore_anchor_sequence": anchor_seq,
        "decisions": [
            {
                "segment_id": d.segment_id,
                "start_sequence": d.start_sequence,
                "end_sequence": d.end_sequence,
                "action": d.action,
                "reasons": list(d.reasons),
            }
            for d in decisions
        ],
        "eligible_segment_ids": list(eligible),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return RetentionPlan(
        REPORT_SCHEMA, restore_head, anchor_seq, decisions, eligible, digest
    )
