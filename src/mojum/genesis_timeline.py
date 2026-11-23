"""Read-only deterministic summaries of GENESIS developmental boundaries.

This module audits a canonical GenomeLedger snapshot. It does not train,
mutate, persist, or claim biological/neural behavior.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .genesis import GenesisError, GenomeLedger, sha256

TIMELINE_SCHEMA = "mojum.genesis.developmental-timeline/1"
MAX_BOUNDARIES = GenomeLedger.MAX_EVENTS + 1

MAX_SNAPSHOT_DEPTH = 8
MAX_SNAPSHOT_NODES = 50_000
MAX_SNAPSHOT_TEXT_BYTES = 5 * 1024 * 1024
MAX_MAPPING_FIELDS = 10


def _preflight_snapshot(value: Any) -> None:
    """Admit a bounded exact-native JSON tree before any mapping lookup.

    This must run before dict.get and before GenomeLedger's canonical
    validator, whose historical key-set checks assume trusted mapping keys.
    """
    stack = [(value, 0)]
    seen: set[int] = set()
    nodes = 0
    text_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SNAPSHOT_NODES or depth > MAX_SNAPSHOT_DEPTH:
            raise GenesisError("Snapshot exceeds structural limits")

        if type(item) is dict:
            if len(item) > MAX_MAPPING_FIELDS:
                raise GenesisError("Snapshot mapping field limit exceeded")
            identity = id(item)
            if identity in seen:
                raise GenesisError("Snapshot contains a cycle or shared container")
            seen.add(identity)
            for key, child in item.items():
                if type(key) is not str or not key or len(key) > 64:
                    raise GenesisError("Snapshot keys must be bounded native strings")
                try:
                    text_bytes += len(key.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise GenesisError("Snapshot key is not valid UTF-8") from exc
                if text_bytes > MAX_SNAPSHOT_TEXT_BYTES:
                    raise GenesisError("Snapshot text budget exceeded")
                stack.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > GenomeLedger.MAX_EVENTS:
                raise GenesisError("Snapshot event limit exceeded")
            identity = id(item)
            if identity in seen:
                raise GenesisError("Snapshot contains a cycle or shared container")
            seen.add(identity)
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > 512:
                raise GenesisError("Snapshot text field exceeds limit")
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise GenesisError("Snapshot text is not valid UTF-8") from exc
            if text_bytes > MAX_SNAPSHOT_TEXT_BYTES:
                raise GenesisError("Snapshot text budget exceeded")
        elif type(item) is int:
            if item.bit_length() > 64:
                raise GenesisError("Snapshot integer exceeds limit")
        elif item is None or type(item) is bool:
            continue
        else:
            raise GenesisError("Snapshot must contain exact native JSON values")


def audit_timeline(snapshot: Mapping[str, Any], *, max_boundaries: int = MAX_BOUNDARIES) -> dict:
    """Return one deterministic phenotype witness per effective tick boundary.

    Tick zero is always represented. Each distinct acquisition
    valid_from_tick adds one boundary. Snapshot integrity, event ordering,
    gene bounds, and provenance fields are validated by GenomeLedger itself.
    max_boundaries bounds report growth independently of the event limit.
    """
    if type(max_boundaries) is not int or not 1 <= max_boundaries <= MAX_BOUNDARIES:
        raise GenesisError(f"max_boundaries must be an integer in [1, {MAX_BOUNDARIES}]")

    # This boundary intentionally accepts only ordinary JSON-shaped built-ins;
    # do not invoke arbitrary Mapping/list subclass behavior while auditing.
    if type(snapshot) is not dict:
        raise GenesisError("Snapshot must be an exact built-in dict")
    _preflight_snapshot(snapshot)
    seed = snapshot.get("seed")
    if type(seed) is not dict or type(seed.get("inherited")) is not dict:
        raise GenesisError("Snapshot seed must use exact built-in dicts")
    events_input = snapshot.get("events")
    if type(events_input) is not list:
        raise GenesisError("Snapshot events must be an exact built-in list")
    # Reject oversized lists before traversing their contents. GenomeLedger
    # enforces the same canonical cap, but this preflight must precede any()
    # so a hostile caller cannot force an unbounded validation scan here.
    if len(events_input) > GenomeLedger.MAX_EVENTS:
        raise GenesisError("Snapshot event limit exceeded")
    if any(type(event) is not dict for event in events_input):
        raise GenesisError("Snapshot events must be an exact built-in list of dicts")

    ledger = GenomeLedger.from_snapshot(snapshot)
    canonical_snapshot = ledger.snapshot()
    events = canonical_snapshot["events"]
    ticks = sorted({0, *(event["valid_from_tick"] for event in events)})
    if len(ticks) > max_boundaries:
        raise GenesisError("Developmental timeline boundary limit exceeded")

    boundaries = []
    for tick in ticks:
        phenotype = ledger.phenotype(tick)
        boundaries.append({
            "tick": tick,
            "parameters": dict(phenotype["parameters"]),
            "applied_acquisition_ids": list(phenotype["applied_acquisition_ids"]),
            "phenotype_sha256": sha256(phenotype),
        })

    report = {
        "schema": TIMELINE_SCHEMA,
        "genome_id": canonical_snapshot["seed"]["genome_id"],
        "ledger_revision": canonical_snapshot["revision"],
        "inherited_seed_sha256": sha256(canonical_snapshot["seed"]),
        "boundaries": boundaries,
    }
    report["report_sha256"] = sha256(report)
    return report
