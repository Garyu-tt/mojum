"""Read-only continuity evidence for PUBLIC_SYNTHETIC GENESIS ledger snapshots.

This does not authenticate an owner, attest consent, or verify disk durability.
It only detects non-append-only changes between two strictly validated snapshots.
"""

from __future__ import annotations

import hashlib
import json

from .genesis import GENES, GenesisError, GenomeLedger, sha256

SCHEMA = "mojum.genesis.append-witness/1"
MAX_EVENTS = 128  # Smaller than GenomeLedger's own 1024: bounded O(n^3) replay.
MAX_SNAPSHOT_BYTES = 262_144
MAX_NODES = 4_096
MAX_DEPTH = 8


class GenesisWitnessError(ValueError):
    """Input cannot be attested as a valid, append-only synthetic ledger pair."""


def _detached_snapshot(value, label):
    """Reject Python subclass/equality gadgets; detach a small JSON tree."""
    stack = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise GenesisWitnessError(f"{label} exceeds structural budget")
        if type(item) is dict:
            if len(item) > 20:
                raise GenesisWitnessError(f"{label} has too many object keys")
            for key, val in item.items():
                if type(key) is not str:
                    raise GenesisWitnessError(f"{label} has non-native string key")
                stack.append((val, depth + 1))
        elif type(item) is list:
            if len(item) > MAX_EVENTS:
                raise GenesisWitnessError(f"{label} list exceeds event budget")
            stack.extend((val, depth + 1) for val in item)
        elif type(item) is str:
            if len(item) > 1_024:
                raise GenesisWitnessError(f"{label} string exceeds budget")
        elif type(item) is int:
            if not -(2**63) <= item < 2**63:
                raise GenesisWitnessError(f"{label} integer exceeds 64-bit bound")
        else:
            # Reject bool, float, None, tuple, custom str/dict/list subclasses.
            raise GenesisWitnessError(f"{label} contains non-canonical native JSON data")
    if type(value) is not dict:
        raise GenesisWitnessError(f"{label} must be a native object")
    if type(value.get("events")) is not list or len(value["events"]) > MAX_EVENTS:
        raise GenesisWitnessError(f"{label} has invalid event count")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeError, TypeError, ValueError, OverflowError) as exc:
        raise GenesisWitnessError(f"{label} is not canonical UTF-8 JSON") from exc
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise GenesisWitnessError(f"{label} exceeds byte budget")
    return json.loads(encoded)


def compare_append_only(before, after, *, classification):
    """Return redacted, stable proof metadata iff ``after`` extends ``before``.

    Both snapshots must be explicit PUBLIC_SYNTHETIC fixtures. History
    identity is checked by exact seed and exact ordered event-prefix equality,
    not by coincidental phenotype or aggregate counters. Revisions are
    content checksums, NOT independent signatures or proof of authorship.
    """
    if type(classification) is not str or classification != "PUBLIC_SYNTHETIC":
        raise GenesisWitnessError("Explicit PUBLIC_SYNTHETIC classification required")
    first = _detached_snapshot(before, "before")
    second = _detached_snapshot(after, "after")
    try:
        a = GenomeLedger.from_snapshot(first)
        b = GenomeLedger.from_snapshot(second)
    except (GenesisError, TypeError, ValueError, OverflowError) as exc:
        raise GenesisWitnessError("Invalid GENESIS ledger snapshot") from exc

    if first["seed"] != second["seed"]:
        raise GenesisWitnessError("Inherited seed changed")
    previous = first["events"]
    current = second["events"]
    if len(current) < len(previous):
        raise GenesisWitnessError("Ledger has rolled back")
    if current[:len(previous)] != previous:
        raise GenesisWitnessError("Ledger is a fork, reorder or history rewrite")

    appended = current[len(previous):]
    # Only bounded synthetic counts and hashed identifiers leave this API.
    counts = {name: 0 for name in sorted(GENES)}
    deltas = {name: 0 for name in sorted(GENES)}
    for event in appended:
        counts[event["gene"]] += 1
        deltas[event["gene"]] += event["delta"]
    id_commitment = hashlib.sha256(json.dumps(
        [event["event_id"] for event in appended], ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "relation": "equal" if not appended else "append_only",
        "inherited_seed_sha256": sha256(first["seed"]),
        "before_revision": a.revision,
        "after_revision": b.revision,
        "before_event_count": len(previous),
        "after_event_count": len(current),
        "appended_event_count": len(appended),
        "appended_event_ids_sha256": id_commitment,
        "appended_gene_counts": counts,
        "appended_gene_delta_sums": deltas,
        "source_classification": "PUBLIC_SYNTHETIC",
        "verification_scope": "content_continuity_only_not_authentication",
    }
