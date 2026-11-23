"""Read-only attribution of inherited and acquired GENESIS parameters.

This diagnostic consumes a PUBLIC_SYNTHETIC ledger snapshot. It never mutates
or persists ledger state and does not authenticate the snapshot's author.
"""
from __future__ import annotations

import json
import math

from mojum.genesis import GENES, GenesisError, GenomeLedger, sha256

ATTRIBUTION_SCHEMA = "mojum.genesis.attribution/1"
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_JSON_NODES = 20_000
MAX_JSON_DEPTH = 32
MAX_TEXT_LENGTH = 8_192
MAX_TICK = 2**63 - 1


class AttributionError(ValueError):
    """Rejected an invalid or non-reconciling attribution request."""


def _check_json_tree(value):
    """Bound and type-check JSON-native input before core validation.

    Exact built-in types avoid invoking caller-defined comparison, iteration,
    hashing or serialization methods while processing an untrusted object.
    """
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise AttributionError("snapshot node limit exceeded")
        if depth > MAX_JSON_DEPTH:
            raise AttributionError("snapshot nesting limit exceeded")
        kind = type(item)
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise AttributionError("snapshot object keys must be strings")
                if len(key) > MAX_TEXT_LENGTH:
                    raise AttributionError("snapshot key too long")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise AttributionError("snapshot key must be valid UTF-8") from exc
                stack.append((child, depth + 1))
        elif kind is list:
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            if len(item) > MAX_TEXT_LENGTH:
                raise AttributionError("snapshot text too long")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise AttributionError("snapshot text must be valid UTF-8") from exc
        elif kind is int:
            if not -(2**63) <= item <= MAX_TICK:
                raise AttributionError("snapshot integer out of range")
        elif kind is float:
            if not math.isfinite(item):
                raise AttributionError("snapshot number must be finite")
        elif kind not in (bool, type(None)):
            raise AttributionError("snapshot must contain JSON-native values")


def audit_attribution(snapshot, tick):
    """Return a deterministic, redacted decomposition for one logical tick.

    The supplied snapshot must be a PUBLIC_SYNTHETIC GENESIS ledger
    dictionary. A full canonical ledger restore is performed before report
    construction. Acquisition identifiers, sources and evidence strings are
    intentionally omitted from the report.
    """
    if type(tick) is not int or not 0 <= tick <= MAX_TICK:
        raise AttributionError("tick must be a nonnegative signed 64-bit integer")
    if type(snapshot) is not dict:
        raise AttributionError("snapshot must be a plain JSON object")
    _check_json_tree(snapshot)
    try:
        encoded = json.dumps(
            snapshot, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise AttributionError("snapshot is not bounded canonical JSON") from exc
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise AttributionError("snapshot byte limit exceeded")

    try:
        ledger = GenomeLedger.from_snapshot(snapshot)
        restored = ledger.snapshot()
        phenotype = ledger.phenotype(tick)
    except (GenesisError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise AttributionError("invalid GENESIS ledger snapshot") from exc

    genes = sorted(GENES)
    inherited = {
        gene: restored["seed"]["inherited"][gene]
        for gene in genes
    }
    delta_by_gene = {gene: 0 for gene in genes}
    count_by_gene = {gene: 0 for gene in genes}
    effective_count = 0
    future_count = 0
    for event in restored["events"]:
        if event["valid_from_tick"] <= tick:
            gene = event["gene"]
            delta_by_gene[gene] += event["delta"]
            count_by_gene[gene] += 1
            effective_count += 1
        else:
            future_count += 1

    reconstructed = {
        gene: inherited[gene] + delta_by_gene[gene]
        for gene in genes
    }
    if reconstructed != phenotype["parameters"]:
        raise AttributionError("acquisition attribution does not reconcile with phenotype")

    report = {
        "schema": ATTRIBUTION_SCHEMA,
        "tick": tick,
        "ledger_revision": restored["revision"],
        "inherited_seed_sha256": phenotype["inherited_seed_sha256"],
        "inherited_parameters": inherited,
        "effective_acquired_delta_by_gene": delta_by_gene,
        "effective_acquisition_count_by_gene": count_by_gene,
        "effective_acquisition_count": effective_count,
        "future_acquisition_count": future_count,
        "effective_parameters": reconstructed,
        "reconciled": True,
        "source_classification": "PUBLIC_SYNTHETIC_DIAGNOSTIC_NOT_AUTHENTICATION",
    }
    report["report_sha256"] = sha256(report)
    return report
