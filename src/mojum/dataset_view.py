"""Deterministic, privacy-first projection of synthetic episode records.

Pure in-memory dataset view; NOT a Noosphere persistence backend.
"""
import hashlib
import json
from collections.abc import Mapping

SCHEMA = "mojum.dataset-view/1"


def project_episodes(episodes, *, allowed_sources, max_records=1000):
    """Canonical PUBLIC records and SHA256; reject malformed/ambiguous data."""
    if not isinstance(max_records, int) or isinstance(max_records, bool) or not 0 <= max_records <= 100000:
        raise ValueError("invalid max_records")
    if not isinstance(allowed_sources, (set, frozenset)) or any(not isinstance(s, str) or not s for s in allowed_sources):
        raise ValueError("invalid allowed_sources")
    if not isinstance(episodes, (list, tuple)) or len(episodes) > 100000:
        raise ValueError("episodes must be a bounded sequence")
    seen = {}
    for event in episodes:
        if not isinstance(event, Mapping) or set(event) != {"id", "source", "visibility", "tick", "observation"}:
            raise ValueError("invalid episode")
        eid, src, visibility, tick, obs = (event[k] for k in ("id", "source", "visibility", "tick", "observation"))
        if not isinstance(eid, str) or not eid or not isinstance(src, str) or not src:
            raise ValueError("invalid identifiers")
        if visibility not in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            raise ValueError("invalid visibility")
        if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
            raise ValueError("invalid tick")
        if not isinstance(obs, str) or len(obs) > 4096:
            raise ValueError("invalid observation")
        if visibility != "PUBLIC" or src not in allowed_sources:
            continue
        record = {"id": eid, "source": src, "tick": tick, "observation": obs}
        if eid in seen and seen[eid] != record:
            raise ValueError("conflicting duplicate id")
        seen[eid] = record
    records = sorted(seen.values(), key=lambda r: (r["tick"], r["id"]))
    if len(records) > max_records:
        raise ValueError("record budget exceeded")
    payload = {"schema": SCHEMA, "records": records}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema": SCHEMA, "records": records, "sha256": hashlib.sha256(canonical).hexdigest()}
