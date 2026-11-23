"""Stable, source-disjoint PUBLIC dataset split for offline synthetic benchmarks.

This module does not read PRIVATE Noosphere journals or run training. It accepts
only an already materialized, canonical mojum.dataset-view/1 PUBLIC projection.
Hashes provide reproducibility/integrity, NOT authentication or consent.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from .dataset_view import SCHEMA as VIEW_SCHEMA, project_episodes

SCHEMA = "mojum.public-dataset-split/1"
ASSIGNMENT = "source-sha256-v1"
MAX_RECORDS = 10_000
_MAX_SEED_BYTES = 256
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PARTITIONS = ("train", "validation", "test")
_WEIGHT_KEYS = frozenset(_PARTITIONS)
_VIEW_KEYS = frozenset({"schema", "records", "sha256"})
_RECORD_KEYS = frozenset({"id", "source", "tick", "observation"})
_SPLIT_KEYS = frozenset({
    "schema", "source_view_sha256", "assignment", "seed",
    "weights", "train", "validation", "test", "manifest_sha256",
})


def _native_keys(value, expected):
    # Check native key types before any equality/hash-based key-set comparison.
    return (type(value) is dict and all(type(key) is str for key in value)
            and value.keys() == expected)


def _native_record(value):
    return (
        _native_keys(value, _RECORD_KEYS)
        and type(value["id"]) is str and 0 < len(value["id"]) <= 256
        and type(value["source"]) is str and 0 < len(value["source"]) <= 256
        and type(value["tick"]) is int and 0 <= value["tick"] <= 2**63 - 1
        and type(value["observation"]) is str
        and len(value["observation"]) <= 4096
    )


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8", errors="strict")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _admit_public_view(view):
    """Verify the complete canonical PUBLIC digest before considering any split."""
    if (not _native_keys(view, _VIEW_KEYS)
            or type(view["schema"]) is not str
            or view["schema"] != VIEW_SCHEMA
            or type(view["sha256"]) is not str
            or _HEX64.fullmatch(view["sha256"]) is None
            or type(view["records"]) is not list
            or len(view["records"]) > MAX_RECORDS):
        raise ValueError("invalid bounded PUBLIC dataset view")

    synthetic = []
    sources = set()
    for record in view["records"]:
        if not _native_record(record):
            raise ValueError("invalid PUBLIC dataset record")
        # Only records with the documented projected wire keys can enter.
        # Do not accept raw episodes, hidden/private visibility fields, or
        # caller-supplied arbitrary mappings with side-effecting iteration.
        sources.add(record["source"])
        synthetic.append({
            "id": record["id"],
            "source": record["source"],
            "tick": record["tick"],
            "observation": record["observation"],
            "visibility": "PUBLIC",
        })

    # Reuse the canonical view's strict UTF-8, timestamp, identity, byte
    # budget and deterministic ordering validation rather than weakening it.
    reconstructed = project_episodes(
        synthetic, allowed_sources=sources, max_records=len(synthetic),
    )
    if (reconstructed["records"] != view["records"]
            or not hmac.compare_digest(reconstructed["sha256"], view["sha256"])):
        raise ValueError("noncanonical or tampered PUBLIC dataset view")
    return reconstructed


def _validate_weights(train, validation, test):
    if (any(type(v) is not int or not 0 <= v <= 100
            for v in (train, validation, test))
            or train + validation + test != 100):
        raise ValueError("split percentages must be integers summing to 100")


def _source_bucket(seed, source):
    # Length-prefixing would also work; a canonical JSON list prevents
    # concatenation ambiguity and the schema/domain pins future revisions.
    digest = hashlib.sha256(_canonical([SCHEMA, ASSIGNMENT, seed, source])).digest()
    return int.from_bytes(digest, "big") % 100


def split_public_view(view, *, seed="mojum-r01", train=80,
                      validation=10, test=10):
    """Return detached versioned partitions with stable source-level assignment.

    All records from one source share a split, regardless of tick, record order,
    or later appended observations. Consequently small or one-source datasets
    can legitimately have empty validation/test partitions; no balancing or
    per-observation leakage-prone fallback is performed.
    """
    if type(seed) is not str or not seed:
        raise ValueError("seed must be a nonempty UTF-8 string")
    try:
        seed_bytes = seed.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("seed must contain valid UTF-8") from exc
    if len(seed_bytes) > _MAX_SEED_BYTES:
        raise ValueError("seed exceeds UTF-8 byte budget")
    _validate_weights(train, validation, test)
    verified = _admit_public_view(view)

    partitioned = {name: [] for name in _PARTITIONS}
    source_assignment = {}
    for record in verified["records"]:
        source = record["source"]
        if source not in source_assignment:
            bucket = _source_bucket(seed, source)
            source_assignment[source] = (
                "train" if bucket < train else
                "validation" if bucket < train + validation else "test"
            )
        partitioned[source_assignment[source]].append(dict(record))

    out = {
        "schema": SCHEMA,
        "source_view_sha256": verified["sha256"],
        "assignment": ASSIGNMENT,
        "seed": seed,
        "weights": {"train": train, "validation": validation, "test": test},
    }
    for name in _PARTITIONS:
        records = partitioned[name]
        out[name] = {
            "schema": VIEW_SCHEMA,
            "records": records,
            "sha256": _digest({"schema": VIEW_SCHEMA, "records": records}),
        }
    out["manifest_sha256"] = _digest(out)
    return out


def verify_public_split(view, split):
    """Recompute the entire split and require exact canonical equality.

    Returns True or raises ValueError; SHA-256 never proves signer identity.
    """
    if (not _native_keys(split, _SPLIT_KEYS)
            or type(split["schema"]) is not str or split["schema"] != SCHEMA
            or type(split["assignment"]) is not str
            or split["assignment"] != ASSIGNMENT
            or type(split["source_view_sha256"]) is not str
            or _HEX64.fullmatch(split["source_view_sha256"]) is None
            or type(split["manifest_sha256"]) is not str
            or _HEX64.fullmatch(split["manifest_sha256"]) is None
            or not _native_keys(split["weights"], _WEIGHT_KEYS)):
        raise ValueError("invalid dataset split manifest")
    # Adversarial in-memory output must be bounded and contain only native
    # scalars before canonical JSON encoding can inspect it.
    count = 0
    for name in _PARTITIONS:
        part = split[name]
        if (not _native_keys(part, _VIEW_KEYS)
                or type(part["schema"]) is not str
                or part["schema"] != VIEW_SCHEMA
                or type(part["sha256"]) is not str
                or _HEX64.fullmatch(part["sha256"]) is None
                or type(part["records"]) is not list):
            raise ValueError("invalid split partition")
        count += len(part["records"])
        if count > MAX_RECORDS or any(not _native_record(row) for row in part["records"]):
            raise ValueError("invalid or overbudget split records")
    weights = split["weights"]
    expected = split_public_view(
        view, seed=split["seed"], train=weights["train"],
        validation=weights["validation"], test=weights["test"],
    )
    # Compare canonical representation, not only a caller-supplied checksum.
    # Exact shapes and types are already enforced by reconstructed output.
    try:
        if _canonical(split) != _canonical(expected):
            raise ValueError("dataset split contents or digest do not match")
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid dataset split encoding or contents") from exc
    return True
