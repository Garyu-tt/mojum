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


def audit_timeline(snapshot: Mapping[str, Any], *, max_boundaries: int = MAX_BOUNDARIES) -> dict:
    """Return one deterministic phenotype witness per effective tick boundary.

    Tick zero is always represented. Each distinct acquisition
    valid_from_tick adds one boundary. Snapshot integrity, event ordering,
    gene bounds, and provenance fields are validated by GenomeLedger itself.
    max_boundaries bounds report growth independently of the event limit.
    """
    if type(max_boundaries) is not int or not 1 <= max_boundaries <= MAX_BOUNDARIES:
        raise GenesisError(f"max_boundaries must be an integer in [1, {MAX_BOUNDARIES}]")

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
