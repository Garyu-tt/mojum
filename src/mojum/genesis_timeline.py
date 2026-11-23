"""Offline, read-only GENESIS phenotype timeline attestations for synthetic fixtures.

A digest here is an integrity fingerprint, NOT a signature or authorization token.
This module deliberately does not change Genesis' ledger, persistence or runtime.
"""

from __future__ import annotations

from mojum.genesis import GENES, GenesisError, GenomeLedger, sha256

SCHEMA = "mojum.genesis.phenotype-timeline/1"
MAX_BOUNDARIES = 256
MAX_TICK = 2**63 - 1


def _tick(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TICK:
        raise GenesisError(f"{field} must be an integer in [0, {MAX_TICK}]")
    return value


def attest_timeline(
    snapshot: dict,
    *,
    start_tick: int,
    through_tick: int,
    public_synthetic: bool = False,
) -> dict:
    """Attest phenotype at the window start/end and each acquired change boundary.

    All inputs originate in an integrity-validated ``GenomeLedger`` snapshot;
    nothing is written back. The caller explicitly certifies synthetic/public
    data. No raw evidence or source text is disclosed in the report: the event
    content hash and separate timestamps provide provenance without duplicating
    the evidence body. ``MAX_BOUNDARIES`` caps output regardless of tick span.
    """
    if public_synthetic is not True:
        raise GenesisError("explicit public_synthetic=True required")
    start = _tick(start_tick, "start_tick")
    end = _tick(through_tick, "through_tick")
    if end < start:
        raise GenesisError("through_tick precedes start_tick")
    if type(snapshot) is not dict:
        raise GenesisError("ledger snapshot must be a dict")

    # Exact ledger revision is checked by from_snapshot; a forged or truncated
    # event collection must not produce a plausible timeline witness.
    ledger = GenomeLedger.from_snapshot(snapshot)
    verified = ledger.snapshot()
    events = verified["events"]
    boundaries = sorted({start, end, *(
        event["valid_from_tick"] for event in events
        if start < event["valid_from_tick"] < end
    )})
    if len(boundaries) > MAX_BOUNDARIES:
        raise GenesisError("phenotype timeline boundary budget exceeded")

    rows = []
    previous = dict(verified["seed"]["inherited"])
    previous_tick = None
    for tick in boundaries:
        phenotype = ledger.phenotype(tick)
        effective = [e for e in events if e["valid_from_tick"] <= tick]
        activated = [e for e in effective if (
            previous_tick is None or e["valid_from_tick"] > previous_tick
        )]
        values = phenotype["parameters"]
        rows.append({
            "tick": tick,
            "parameters": values,
            "delta_since_previous": {
                gene: values[gene] - previous[gene] for gene in GENES
            },
            "effective_acquisition_count": len(effective),
            "effective_event_ids_sha256": sha256([e["event_id"] for e in effective]),
            "newly_effective": [{
                "event_id": e["event_id"],
                "gene": e["gene"],
                "delta": e["delta"],
                "valid_from_tick": e["valid_from_tick"],
                "observed_at_ns": e["observed_at_ns"],
                "recorded_at_ns": e["recorded_at_ns"],
                "event_sha256": sha256(e),
            } for e in activated],
        })
        previous = values
        previous_tick = tick

    report = {
        "schema": SCHEMA,
        "data_classification": "PUBLIC_SYNTHETIC",
        "integrity_only_not_authority": True,
        "genome_id": verified["seed"]["genome_id"],
        "ledger_revision": verified["revision"],
        "inherited_seed_sha256": sha256(verified["seed"]),
        "start_tick": start,
        "through_tick": end,
        "boundaries": rows,
    }
    # Caller can independently rehash the report, but this does not imply an
    # authenticated signer or independently trusted observation provenance.
    report["sha256"] = sha256(report)
    return report
