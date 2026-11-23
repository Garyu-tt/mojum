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



def _native_object(value: object, label: str) -> dict:
    # Check keys before set(), dictionary equality or ledger JSON serialization:
    # a str subclass can run arbitrary equality code during those operations.
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise GenesisError(f"{label} requires a native JSON object with string keys")
    return value


def _native_text_fields(record: dict, fields: tuple[str, ...], label: str) -> None:
    for key in fields:
        if key in record and type(record[key]) is not str:
            raise GenesisError(f"{label}.{key} must be a native JSON string")


def _preflight_native_snapshot(snapshot: object) -> None:
    """Reject Python object forgery before the underlying ledger uses equality.

    The existing ledger remains authoritative for schema, value ranges, event
    ordering and revision replay. This only enforces the primitive wire types
    that a decoded JSON document would have, with its existing event quota.
    """
    record = _native_object(snapshot, "ledger")
    _native_text_fields(record, ("schema", "revision"), "ledger")
    seed = _native_object(record.get("seed"), "ledger.seed")
    _native_text_fields(seed, ("schema", "genome_id", "revision"), "ledger.seed")
    inherited = _native_object(seed.get("inherited"), "ledger.seed.inherited")
    if any(type(value) is not int for value in inherited.values()):
        raise GenesisError("ledger.seed.inherited requires native integer values")
    events = record.get("events")
    if type(events) is not list or len(events) > GenomeLedger.MAX_EVENTS:
        raise GenesisError("ledger.events requires a bounded native JSON list")
    for event in events:
        entry = _native_object(event, "ledger.event")
        _native_text_fields(
            entry, ("schema", "event_id", "genome_id", "gene",
                    "source", "evidence"), "ledger.event",
        )
        for key in ("delta", "valid_from_tick", "observed_at_ns", "recorded_at_ns"):
            if key in entry and type(entry[key]) is not int:
                raise GenesisError(f"ledger.event.{key} must be a native integer")


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
    _preflight_native_snapshot(snapshot)
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
