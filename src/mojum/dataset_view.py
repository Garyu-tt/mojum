"""Deterministic, privacy-first projection of synthetic episode records.

Pure in-memory dataset view; NOT a Noosphere persistence backend.
"""
import hashlib
import json
from collections.abc import Mapping

SCHEMA = "mojum.dataset-view/1"
# Bound the serialized PUBLIC view, not merely the number of records. The
# encoder streams into SHA-256 so a large export need not make a second copy.
_MAX_CANONICAL_BYTES = 16 * 1024 * 1024


def _require_utf8(value, label):
    """Reject unpaired surrogates rather than leaking UnicodeEncodeError."""
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"invalid UTF-8 in {label}") from exc


def _hash_canonical_bounded(payload):
    digest = hashlib.sha256()
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for fragment in encoder.iterencode(payload):
        try:
            encoded = fragment.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("invalid UTF-8 in projected record") from exc
        size += len(encoded)
        if size > _MAX_CANONICAL_BYTES:
            raise ValueError("canonical PUBLIC view byte budget exceeded")
        digest.update(encoded)
    return digest.hexdigest()



def project_episodes(episodes, *, allowed_sources, max_records=1000):
    """Canonical PUBLIC records and SHA256; reject malformed/ambiguous data."""
    if not isinstance(max_records, int) or isinstance(max_records, bool) or not 0 <= max_records <= 100000:
        raise ValueError("invalid max_records")
    if not isinstance(allowed_sources, (set, frozenset)) or any(not isinstance(s, str) or not s for s in allowed_sources):
        raise ValueError("invalid allowed_sources")
    for src in allowed_sources:
        _require_utf8(src, "allowed_sources")
    if not isinstance(episodes, (list, tuple)) or len(episodes) > 100000:
        raise ValueError("episodes must be a bounded sequence")
    seen = {}
    identities = {}
    # Count the exact canonical wire size as records are admitted; fail early
    # instead of first retaining a potentially hundreds-of-MiB PUBLIC view.
    projected_bytes = len(json.dumps({"schema": SCHEMA, "records": []},
                                     ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8"))
    for event in episodes:
        if not isinstance(event, Mapping) or set(event) != {"id", "source", "visibility", "tick", "observation"}:
            raise ValueError("invalid episode")
        eid, src, visibility, tick, obs = (event[k] for k in ("id", "source", "visibility", "tick", "observation"))
        if not isinstance(eid, str) or not eid or not isinstance(src, str) or not src:
            raise ValueError("invalid identifiers")
        _require_utf8(eid, "episode id")
        _require_utf8(src, "episode source")
        if visibility not in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            raise ValueError("invalid visibility")
        if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
            raise ValueError("invalid tick")
        if not isinstance(obs, str) or len(obs) > 4096:
            raise ValueError("invalid observation")
        _require_utf8(obs, "episode observation")
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
        if eid not in seen:
            wire = json.dumps(record, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
            projected_bytes += len(wire) + (1 if seen else 0)
            if projected_bytes > _MAX_CANONICAL_BYTES:
                raise ValueError("canonical PUBLIC view byte budget exceeded")
        seen[eid] = record
    records = sorted(seen.values(), key=lambda r: (r["tick"], r["id"]))
    if len(records) > max_records:
        raise ValueError("record budget exceeded")
    payload = {"schema": SCHEMA, "records": records}
    return {"schema": SCHEMA, "records": records, "sha256": _hash_canonical_bounded(payload)}
