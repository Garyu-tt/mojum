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


def _json_string_size(value):
    """Return the exact UTF-8 size of json.dumps' quoted string form."""
    size = 2
    short_escapes = {8, 9, 10, 12, 13}
    for char in value:
        code = ord(char)
        if code in (34, 92):
            size += 2
        elif code < 32:
            size += 2 if code in short_escapes else 6
        elif 0xD800 <= code <= 0xDFFF:
            raise AttributionError("snapshot text must be valid UTF-8")
        elif code <= 0x7F:
            size += 1
        elif code <= 0x7FF:
            size += 2
        elif code <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > MAX_SNAPSHOT_BYTES:
            raise AttributionError("snapshot byte limit exceeded")
    return size


def _check_json_tree(value):
    """Preflight compact JSON byte size before allocating serialized input.

    Exact built-in types avoid invoking caller-defined comparison, iteration,
    hashing or serialization methods while processing an untrusted object.
    """
    stack = [(value, 0)]
    nodes = 0
    encoded_size = 0

    def add_size(amount):
        nonlocal encoded_size
        encoded_size += amount
        if encoded_size > MAX_SNAPSHOT_BYTES:
            raise AttributionError("snapshot byte limit exceeded")

    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise AttributionError("snapshot node limit exceeded")
        if depth > MAX_JSON_DEPTH:
            raise AttributionError("snapshot nesting limit exceeded")
        kind = type(item)
        if kind is dict:
            if nodes + len(stack) + len(item) > MAX_JSON_NODES:
                raise AttributionError("snapshot node limit exceeded")
            add_size(2 + max(0, len(item) - 1))
            for key, child in item.items():
                if type(key) is not str:
                    raise AttributionError("snapshot object keys must be strings")
                if len(key) > MAX_TEXT_LENGTH:
                    raise AttributionError("snapshot key too long")
                add_size(_json_string_size(key) + 1)
                stack.append((child, depth + 1))
        elif kind is list:
            if nodes + len(stack) + len(item) > MAX_JSON_NODES:
                raise AttributionError("snapshot node limit exceeded")
            add_size(2 + max(0, len(item) - 1))
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            if len(item) > MAX_TEXT_LENGTH:
                raise AttributionError("snapshot text too long")
            add_size(_json_string_size(item))
        elif kind is int:
            if not -(2**63) <= item <= MAX_TICK:
                raise AttributionError("snapshot integer out of range")
            add_size(len(str(item)))
        elif kind is float:
            if not math.isfinite(item):
                raise AttributionError("snapshot number must be finite")
            add_size(len(repr(item)))
        elif kind is bool:
            add_size(4 if item else 5)
        elif item is None:
            add_size(4)
        else:
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
