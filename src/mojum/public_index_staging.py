"""PUBLIC-only index staging from an already verified Noosphere dataset view.

This never imports sqlite-vec or embeds PRIVATE data. Source authentication,
visibility authorization and vector persistence belong to later adapters.
"""
from __future__ import annotations

import hashlib
import json


_SCHEMA = "mojum.dataset-view/1"
_OUTPUT = "mojum.public-index-staging/1"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def stage_public_index(dataset: object, *, max_records: int = 1024) -> dict:
    """Validate a canonical PUBLIC projection and derive stable index keys.

    Reject anything that is not the exact output shape of project_episodes.
    A matching SHA-256 is a checksum, *not* proof of a trusted data source.
    """
    if type(max_records) is not int or not 0 <= max_records <= 1024:
        raise ValueError("invalid staging record budget")
    if type(dataset) is not dict or set(dataset) != {"schema", "records", "sha256"}:
        raise ValueError("expected a PUBLIC dataset projection")
    if dataset["schema"] != _SCHEMA:
        raise ValueError("unsupported PUBLIC dataset schema")
    expected_sha = dataset["sha256"]
    if (type(expected_sha) is not str or len(expected_sha) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha)):
        raise ValueError("invalid projection checksum")
    records = dataset["records"]
    if type(records) is not list or len(records) > max_records:
        raise ValueError("too many PUBLIC records")
    entries = []
    prev_order = None
    seen = set()
    for record in records:
        if type(record) is not dict or set(record) != {
            "id", "source", "tick", "observation",
        }:
            raise ValueError("unexpected indexed record fields")
        rid, source = record["id"], record["source"]
        tick, observation = record["tick"], record["observation"]
        if (type(rid) is not str or not 0 < len(rid) <= 256
                or type(source) is not str or not 0 < len(source) <= 256
                or type(tick) is not int or not 0 <= tick <= 2**63 - 1
                or type(observation) is not str or len(observation) > 4096):
            raise ValueError("malformed indexed PUBLIC record")
        order = (tick, rid)
        if order <= prev_order if prev_order is not None else False:
            raise ValueError("unsorted or duplicate index records")
        if rid in seen:
            raise ValueError("duplicate record id")
        seen.add(rid)
        prev_order = order
        try:
            stable_key = hashlib.sha256(_canonical([source, rid, tick])).hexdigest()
            text_hash = hashlib.sha256(observation.encode("utf-8", errors="strict")).hexdigest()
        except UnicodeEncodeError as exc:
            raise ValueError("invalid indexed UTF-8") from exc
        entries.append({
            "key": stable_key,
            "event_id": rid,
            "source": source,
            "tick": tick,
            "observation": observation,
            "text_sha256": text_hash,
        })
    try:
        actual_sha = hashlib.sha256(_canonical({
            "schema": _SCHEMA, "records": records,
        })).hexdigest()
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        raise ValueError("noncanonical PUBLIC projection") from exc
    if actual_sha != expected_sha:
        raise ValueError("PUBLIC projection checksum mismatch")
    return {
        "schema": _OUTPUT,
        "projection_sha256": expected_sha,
        "entries": entries,
        "source_integrity": "checksum-only-not-authenticated",
    }
