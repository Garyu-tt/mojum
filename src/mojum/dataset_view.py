"""Deterministic, privacy-first projection of synthetic episode records.

Pure in-memory dataset view; NOT a Noosphere persistence backend.
"""
import hashlib
import json
from collections.abc import Mapping

SCHEMA = "mojum.dataset-view/1"


def _text(value, field, *, max_length, allow_empty=False):
    """Only bounded, UTF-8-encodable strings may enter canonical exports.

    Python strings can contain lone surrogate code points; json.dumps with
    ensure_ascii=False accepts them, but encoding the result as UTF-8 fails.
    Check hidden records too so filtering does not hide malformed input.
    """
    if (not isinstance(value, str) or len(value) > max_length
            or (not allow_empty and not value)):
        raise ValueError(f"invalid {field}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"invalid {field}: not UTF-8 encodable") from exc
    return value


def project_episodes(episodes, *, allowed_sources, max_records=1000):
    """Canonical PUBLIC records and SHA256; reject malformed/ambiguous data."""
    if not isinstance(max_records, int) or isinstance(max_records, bool) or not 0 <= max_records <= 100000:
        raise ValueError("invalid max_records")
    if not isinstance(allowed_sources, (set, frozenset)):
        raise ValueError("invalid allowed_sources")
    for source in allowed_sources:
        _text(source, "allowed_sources", max_length=256)
    if not isinstance(episodes, (list, tuple)) or len(episodes) > 100000:
        raise ValueError("episodes must be a bounded sequence")
    seen = {}
    identities = {}
    for event in episodes:
        if not isinstance(event, Mapping) or set(event) != {"id", "source", "visibility", "tick", "observation"}:
            raise ValueError("invalid episode")
        eid, src, visibility, tick, obs = (event[k] for k in ("id", "source", "visibility", "tick", "observation"))
        _text(eid, "id", max_length=256)
        _text(src, "source", max_length=256)
        if visibility not in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            raise ValueError("invalid visibility")
        if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
            raise ValueError("invalid tick")
        _text(obs, "observation", max_length=4096, allow_empty=True)
        # Check global event identity *before* applying the visibility/source
        # policy. Otherwise a PUBLIC copy of a PRIVATE event ID can silently
        # replace it in an export, hiding a conflicting classification.
        identity = (src, visibility, tick, obs)
        if eid in identities and identities[eid] != identity:
            raise ValueError("conflicting duplicate id")
        identities[eid] = identity
        if visibility != "PUBLIC" or src not in allowed_sources:
            continue
        record = {"id": eid, "source": src, "tick": tick, "observation": obs}
        seen[eid] = record
    records = sorted(seen.values(), key=lambda r: (r["tick"], r["id"]))
    if len(records) > max_records:
        raise ValueError("record budget exceeded")
    payload = {"schema": SCHEMA, "records": records}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema": SCHEMA, "records": records, "sha256": hashlib.sha256(canonical).hexdigest()}
