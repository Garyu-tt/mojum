"""Read-only ancestry/fork audit of two real synthetic Aquarium cold archives.

No replication, ownership grant, signatures, consensus or automatic recovery.
Private event payloads are inspected in memory but never returned or printed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re

from .checkpoint_handoff import read_verified_archive
from .cold_aquarium import (
    MEMORY_SCHEMA, _parse_component, _validated_payloads,
)
from .noosphere import NoosphereError, _sha, _validate_event

_ZERO = "0" * 64
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TOPICS = frozenset(("aquarium.bootstrap", "aquarium.frame", "genesis.acquisition"))


@dataclass(frozen=True)
class JournalEvidence:
    """Validated immutable event hashes, not a claim of trusted authorship."""

    organism_id: str
    tick: int
    manifest_sha256: str
    head: str
    event_hashes: tuple[str, ...]


def _validated_hash_chain(memory, owner, expected_tick, bootstrap):
    """Recompute every Noosphere event hash from the private archived bytes."""
    items = memory["items"]
    previous = _ZERO
    last_recorded = -1
    seen_ids = set()
    frame_count = 0
    hashes = []

    for i, item in enumerate(items, 1):
        if (type(item) is not dict or set(item) != {"seq", "event", "event_hash"}
                or type(item["seq"]) is not int or item["seq"] != i
                or type(item["event_hash"]) is not str
                or _SHA.fullmatch(item["event_hash"]) is None):
            raise ValueError("invalid archived journal entry or sequence")
        try:
            event = _validate_event(item["event"], owner)
        except (NoosphereError, TypeError, OverflowError, RecursionError) as exc:
            raise ValueError("invalid archived Noosphere event") from exc
        event_id = event["event_id"]
        if event_id in seen_ids or event["recorded_at_ns"] < last_recorded:
            raise ValueError("duplicate event or nonmonotonic recorded time")
        seen_ids.add(event_id)
        if (event["visibility"] != "PRIVATE"
                or event["source"] != "mojum.aquarium.fixture"
                or event["topic"] not in _TOPICS):
            raise ValueError("unexpected archived event ownership or topic")
        if i == 1:
            if (event["topic"] != "aquarium.bootstrap"
                    or event_id != "aquarium-bootstrap"
                    or event["payload"] != bootstrap):
                raise ValueError("archived bootstrap does not match Aquarium")
        elif event["topic"] == "aquarium.bootstrap":
            raise ValueError("duplicate Aquarium bootstrap")
        if event["topic"] == "aquarium.frame":
            if (event_id != f"frame-{frame_count:012d}"
                    or type(event["payload"].get("tick")) is not int
                    or event["payload"]["tick"] != frame_count):
                raise ValueError("unexpected frame sequence")
            frame_count += 1
        calculated = _sha({"previous": previous, "event": event})
        if calculated != item["event_hash"]:
            raise ValueError("archived event hash chain mismatch")
        hashes.append(calculated)
        previous = calculated
        last_recorded = event["recorded_at_ns"]

    if (not hashes or frame_count != expected_tick
            or memory["count"] != len(hashes) or memory["head"] != previous):
        raise ValueError("journal head, event count or tick mismatch")
    return tuple(hashes)


def inspect_archive(path, *, expected_organism_id, expected_tick,
                    expected_manifest_sha256=None):
    """Inspect one pinned cold snapshot; return hashes only, never private events.

    Requires caller-supplied identity and exact tick. Without a separately
    held expected_manifest_sha256, internal hashes prove only consistency.
    """
    if type(expected_tick) is not int or expected_tick < 0:
        raise ValueError("expected_tick must be a nonnegative integer")
    if type(expected_organism_id) is not str or not expected_organism_id:
        raise ValueError("expected organism identity is required")
    manifest, parts = read_verified_archive(
        path,
        expected_organism_id=expected_organism_id,
        expected_revision=expected_tick,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    _, _, world, memory, _ = _validated_payloads(
        parts, expected_id=expected_organism_id, expected_tick=expected_tick,
    )
    hashes = _validated_hash_chain(
        memory, expected_organism_id, expected_tick, world["manifest"],
    )
    return JournalEvidence(
        organism_id=expected_organism_id,
        tick=expected_tick,
        manifest_sha256=manifest["manifest_sha256"],
        head=hashes[-1],
        event_hashes=hashes,
    )


def compare_evidence(left, right):
    """Pure deterministic ancestry classification; never elect a writer."""
    if not isinstance(left, JournalEvidence) or not isinstance(right, JournalEvidence):
        raise ValueError("two validated journal evidence objects are required")
    if left.organism_id != right.organism_id:
        raise ValueError("cannot compare two different organisms")
    common = 0
    for a, b in zip(left.event_hashes, right.event_hashes):
        if a != b:
            break
        common += 1
    if common == 0:
        classification = "UNRELATED_ROOT_STOP"
    elif (common == len(left.event_hashes)
          and common == len(right.event_hashes)):
        classification = "IDENTICAL_HISTORY"
    elif common == len(left.event_hashes):
        classification = "LEFT_ANCESTOR"
    elif common == len(right.event_hashes):
        classification = "RIGHT_ANCESTOR"
    else:
        classification = "FORK_DETECTED_STOP"
    return {
        "schema": "mojum.continuity.journal-audit/1",
        "organism_id": left.organism_id,
        "classification": classification,
        "common_events": common,
        "last_common_head": (left.event_hashes[common - 1] if common else None),
        "left": {"tick": left.tick, "events": len(left.event_hashes),
                 "head": left.head, "manifest_sha256": left.manifest_sha256},
        "right": {"tick": right.tick, "events": len(right.event_hashes),
                  "head": right.head, "manifest_sha256": right.manifest_sha256},
        "requires_conflict_resolution": classification in (
            "FORK_DETECTED_STOP", "UNRELATED_ROOT_STOP",
        ),
        # Even a strict descendant does NOT prove which device has authority.
        "automatic_writer_promotion_allowed": False,
        "trust": "verified_hash_ancestry_not_authenticated_writer_authority",
    }


def audit_archives(left_archive, right_archive, *, expected_organism_id,
                   left_tick, right_tick, left_manifest_sha256=None,
                   right_manifest_sha256=None):
    left = inspect_archive(
        left_archive, expected_organism_id=expected_organism_id,
        expected_tick=left_tick, expected_manifest_sha256=left_manifest_sha256,
    )
    right = inspect_archive(
        right_archive, expected_organism_id=expected_organism_id,
        expected_tick=right_tick, expected_manifest_sha256=right_manifest_sha256,
    )
    return compare_evidence(left, right)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare two PRIVATE Mojum cold archives; never elects a writer",
    )
    parser.add_argument("--left-archive", required=True)
    parser.add_argument("--right-archive", required=True)
    parser.add_argument("--organism-id", required=True)
    parser.add_argument("--left-tick", type=int, required=True)
    parser.add_argument("--right-tick", type=int, required=True)
    parser.add_argument("--left-manifest-sha256")
    parser.add_argument("--right-manifest-sha256")
    args = parser.parse_args(argv)
    try:
        result = audit_archives(
            args.left_archive, args.right_archive,
            expected_organism_id=args.organism_id,
            left_tick=args.left_tick, right_tick=args.right_tick,
            left_manifest_sha256=args.left_manifest_sha256,
            right_manifest_sha256=args.right_manifest_sha256,
        )
    except (OSError, ValueError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    # Conflict is an explicit nonzero result for CI/gating callers.
    return 3 if result["requires_conflict_resolution"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
