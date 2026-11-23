"""Read-only integrity audit for versioned synthetic sensory retention snapshots.

This module validates conservation and chronology invariants in an existing
association/2 snapshot. It does not load or mutate an agent, open SQLite,
perform migrations, authenticate provenance, or infer real-world perception.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

SCHEMA = "mojum.sensory-retention-audit/1"
SOURCE_SCHEMA = "mojum.sensory-curiosity.association/2"
MAX_CUES = 128
MAX_UPDATES = 100_000
MAX_TICK = 2**63 - 1
_CUE = re.compile(r"(?:0|[1-9][0-9]{0,2}):(?:0|[1-9][0-9]{0,2}):(?:0|[1-9][0-9]{0,2})\\Z", re.ASCII)
_EXPECTED_KEYS = {
    "schema", "table", "updates", "last_seen_at", "last_contact_tick",
    "evictions", "forgotten_observations",
}


class RetentionAuditError(ValueError):
    """The supplied association snapshot cannot be safely attested."""


def _invalid() -> RetentionAuditError:
    return RetentionAuditError("invalid association retention evidence")


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def audit_snapshot(snapshot: object) -> dict[str, object]:
    """Return a deterministic, redacted report for one association/2 snapshot.

    Revalidate the raw structure instead of trusting the model loader. Exact
    builtin types are required before comparisons/iteration so hostile
    subclasses cannot run custom equality or conversion code during audit.
    """
    if type(snapshot) is not dict:
        raise _invalid()
    if any(type(key) is not str for key in snapshot):
        raise _invalid()
    if set(snapshot) != _EXPECTED_KEYS:
        raise _invalid()

    schema = snapshot["schema"]
    table = snapshot["table"]
    last_seen = snapshot["last_seen_at"]
    updates = snapshot["updates"]
    last_tick = snapshot["last_contact_tick"]
    evictions = snapshot["evictions"]
    forgotten = snapshot["forgotten_observations"]

    if (
        type(schema) is not str or schema != SOURCE_SCHEMA
        or type(table) is not dict or type(last_seen) is not dict
        or len(table) > MAX_CUES
        or type(updates) is not int or not 0 <= updates <= MAX_UPDATES
        or type(last_tick) is not int or not -1 <= last_tick <= MAX_TICK
        or (updates == 0) != (last_tick == -1)
        or updates > last_tick + 1
        or type(evictions) is not int or not 0 <= evictions <= updates
        or type(forgotten) is not int or not 0 <= forgotten <= updates
        or evictions > max(0, updates - MAX_CUES)
        or evictions > forgotten
    ):
        raise _invalid()
    if (
        any(type(key) is not str for key in table)
        or any(type(key) is not str for key in last_seen)
        or set(table) != set(last_seen)
    ):
        raise _invalid()

    retained_observations = 0
    stamps: list[int] = []
    lru_rows: list[tuple[int, str, int]] = []
    for cue, entry in table.items():
        if (
            type(cue) is not str or len(cue) > 11 or _CUE.fullmatch(cue) is None
            or any(int(part) > 255 for part in cue.split(":"))
            or type(entry) is not dict
            or any(type(key) is not str for key in entry)
            or set(entry) != {"count", "mean_deflection"}
        ):
            raise _invalid()
        count = entry["count"]
        mean = entry["mean_deflection"]
        stamp = last_seen[cue]
        if (
            type(count) is not int or not 1 <= count <= MAX_UPDATES
            or not _finite_number(mean) or not 0 <= mean <= 1
            or type(stamp) is not int or not 0 <= stamp <= last_tick
            or count > stamp + 1
        ):
            raise _invalid()
        retained_observations += count
        stamps.append(stamp)
        lru_rows.append((stamp, cue, count))

    if len(set(stamps)) != len(stamps):
        raise _invalid()
    if (
        retained_observations + forgotten != updates
        or (updates > 0 and (not table or max(stamps) != last_tick))
        or (evictions == 0 and forgotten != 0)
    ):
        raise _invalid()

    # Source policy is deterministic oldest-first; cue is a final tie-breaker.
    lru_rows.sort(key=lambda row: (row[0], row[1]))
    oldest_age = last_tick - lru_rows[0][0] if lru_rows else None
    newest_age = last_tick - lru_rows[-1][0] if lru_rows else None
    canonical = json.dumps(
        snapshot, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256(canonical).hexdigest()

    return {
        "schema": SCHEMA,
        "source_schema": SOURCE_SCHEMA,
        "valid": True,
        "updates": updates,
        "retained_cues": len(table),
        "retained_observations": retained_observations,
        "forgotten_observations": forgotten,
        "evictions": evictions,
        "last_contact_tick": last_tick,
        "oldest_retained_age_ticks": oldest_age,
        "newest_retained_age_ticks": newest_age,
        "snapshot_sha256": digest,
    }
