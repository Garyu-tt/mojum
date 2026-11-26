"""Versioned bounded contact-association retention primitive (opt-in, synthetic-only).

This module is deliberately separate from sensory_play.ContactAssociations/1.
It stores aggregate synthetic observations, never raw sensor frames or private data.
"""
from __future__ import annotations

import copy
import hashlib
import json

SCHEMA = "mojum.sensory.contact-retention/2"
MAX_CUES = 4096
MAX_RETENTION_TICKS = 10_000_000
MAX_COUNT = 2**31 - 1
MAX_TOTAL = 2**63 - 1


class RetentionError(ValueError):
    """Invalid, stale, oversized, or inconsistent retention input."""


def _exact_int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise RetentionError("value is not canonical JSON data") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class ContactRetentionV2:
    """Bounded LRU-by-logical-tick cue aggregates with deterministic TTL expiry.

    Inputs are caller-declared PUBLIC_SYNTHETIC measurements. Values use integer
    milli-units in [0, 1000]. The ledger is a diagnostic learning primitive,
    not a sensor, reward, actuator, or authoritative organism journal.
    """

    def __init__(
        self, *, max_cues: int = 128, retention_ticks: int = 10_000,
        max_observations_per_cue: int = 1_000_000,
    ) -> None:
        if not _exact_int(max_cues, 1, MAX_CUES):
            raise RetentionError("max_cues must be an exact bounded integer")
        if not _exact_int(retention_ticks, 1, MAX_RETENTION_TICKS):
            raise RetentionError("retention_ticks must be an exact bounded integer")
        if not _exact_int(max_observations_per_cue, 1, MAX_COUNT):
            raise RetentionError("max_observations_per_cue must be an exact bounded integer")
        self.max_cues = max_cues
        self.retention_ticks = retention_ticks
        self.max_observations_per_cue = max_observations_per_cue
        self.last_tick = -1
        self.entries: dict[str, dict[str, int]] = {}
        self.total_admitted = 0
        self.expired_cues = 0
        self.capacity_evictions = 0

    def _expire(self, tick: int) -> int:
        stale = sorted(
            cue for cue, entry in self.entries.items()
            if tick - entry["last_tick"] > self.retention_ticks
        )
        for cue in stale:
            del self.entries[cue]
        self.expired_cues += len(stale)
        return len(stale)

    def observe(self, *, cue: str, value_milli: int, tick: int,
                provenance: str = "PUBLIC_SYNTHETIC") -> dict[str, object]:
        if provenance != "PUBLIC_SYNTHETIC" or type(provenance) is not str:
            raise RetentionError("explicit PUBLIC_SYNTHETIC provenance required")
        if type(cue) is not str or not 1 <= len(cue) <= 64:
            raise RetentionError("cue must be a bounded exact string")
        try:
            cue.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RetentionError("cue must be valid UTF-8") from exc
        if not _exact_int(value_milli, 0, 1000):
            raise RetentionError("value_milli must be an exact integer in [0, 1000]")
        if not _exact_int(tick, 0, MAX_TOTAL) or tick <= self.last_tick:
            raise RetentionError("tick must be a strictly increasing bounded integer")
        if self.total_admitted >= MAX_TOTAL:
            raise RetentionError("total observation counter exhausted")

        expired = self._expire(tick)
        evicted = None
        if cue not in self.entries and len(self.entries) >= self.max_cues:
            # Stable LRU: oldest last_tick, then lexicographically smallest cue.
            evicted = min(self.entries, key=lambda key: (
                self.entries[key]["last_tick"], key
            ))
            del self.entries[evicted]
            self.capacity_evictions += 1

        entry = self.entries.get(cue)
        if entry is None:
            self.entries[cue] = {
                "count": 1, "mean_milli": value_milli,
                "first_tick": tick, "last_tick": tick,
            }
        else:
            count = min(entry["count"] + 1, self.max_observations_per_cue)
            # Integer EMA with deterministic nearest rounding; count saturates
            # to keep the aggregate bounded without discarding new evidence.
            delta = value_milli - entry["mean_milli"]
            denominator = min(entry["count"] + 1, self.max_observations_per_cue)
            numerator = entry["mean_milli"] * (denominator - 1) + value_milli
            mean = (numerator + denominator // 2) // denominator
            if entry["count"] >= self.max_observations_per_cue:
                # Once saturated, use a stable 1/N update rather than overflow.
                mean = entry["mean_milli"] + (
                    (delta + (self.max_observations_per_cue // 2 if delta >= 0
                              else -(self.max_observations_per_cue // 2)))
                    // self.max_observations_per_cue
                )
            entry.update(count=count, mean_milli=mean, last_tick=tick)

        self.last_tick = tick
        self.total_admitted += 1
        return {
            "schema": SCHEMA, "accepted": True, "cue": cue, "tick": tick,
            "expired_cues": expired, "evicted_cue": evicted,
            "retained_cues": len(self.entries),
            "retained_observations": sum(item["count"] for item in self.entries.values()),
            "digest": self.digest(),
        }

    def report(self) -> dict[str, object]:
        entries = [
            {"cue": cue, **self.entries[cue]}
            for cue in sorted(self.entries)
        ]
        return {
            "schema": SCHEMA, "last_tick": self.last_tick,
            "max_cues": self.max_cues, "retention_ticks": self.retention_ticks,
            "max_observations_per_cue": self.max_observations_per_cue,
            "entries": entries, "total_admitted": self.total_admitted,
            "expired_cues": self.expired_cues,
            "capacity_evictions": self.capacity_evictions,
        }

    def digest(self) -> str:
        return _digest(self.report())

    def snapshot(self) -> dict[str, object]:
        report = self.report()
        return {**copy.deepcopy(report), "digest": _digest(report)}

    @classmethod
    def from_snapshot(cls, snapshot: object) -> "ContactRetentionV2":
        keys = {
            "schema", "last_tick", "max_cues", "retention_ticks",
            "max_observations_per_cue", "entries", "total_admitted",
            "expired_cues", "capacity_evictions", "digest",
        }
        # Reject cardinality and non-native keys before hashing/comparison.
        if type(snapshot) is not dict or len(snapshot) != len(keys):
            raise RetentionError("invalid v2 snapshot shape")
        for key in snapshot:
            if type(key) is not str or not key or len(key) > 64:
                raise RetentionError("invalid snapshot key")
        if set(snapshot.keys()) != keys:
            raise RetentionError("invalid v2 snapshot shape")

        schema = snapshot["schema"]
        digest = snapshot["digest"]
        if type(schema) is not str or schema != SCHEMA:
            raise RetentionError("unsupported contact-retention schema")
        if (type(digest) is not str or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise RetentionError("invalid snapshot digest")

        max_cues = snapshot["max_cues"]
        retention_ticks = snapshot["retention_ticks"]
        max_observations = snapshot["max_observations_per_cue"]
        if (not _exact_int(max_cues, 1, MAX_CUES)
                or not _exact_int(retention_ticks, 1, MAX_RETENTION_TICKS)
                or not _exact_int(max_observations, 1, MAX_COUNT)):
            raise RetentionError("invalid snapshot limits")
        last_tick = snapshot["last_tick"]
        total_admitted = snapshot["total_admitted"]
        expired_cues = snapshot["expired_cues"]
        capacity_evictions = snapshot["capacity_evictions"]
        if (not _exact_int(last_tick, -1, MAX_TOTAL)
                or not _exact_int(total_admitted, 0, MAX_TOTAL)
                or not _exact_int(expired_cues, 0, MAX_TOTAL)
                or not _exact_int(capacity_evictions, 0, MAX_TOTAL)):
            raise RetentionError("invalid snapshot counters")

        entries = snapshot["entries"]
        if type(entries) is not list or len(entries) > max_cues:
            raise RetentionError("invalid snapshot entry collection")

        normalized_entries: list[dict[str, object]] = []
        seen: set[str] = set()
        retained_observations = 0
        entry_keys = {"cue", "count", "mean_milli", "first_tick", "last_tick"}
        for index, item in enumerate(entries):
            if type(item) is not dict or len(item) != len(entry_keys):
                raise RetentionError("invalid cue entry")
            for key in item:
                if type(key) is not str or not key or len(key) > 32:
                    raise RetentionError("invalid cue entry key")
            if set(item.keys()) != entry_keys:
                raise RetentionError("invalid cue entry")
            cue = item["cue"]
            if type(cue) is not str or not 1 <= len(cue) <= 64 or cue in seen:
                raise RetentionError("invalid or duplicate cue")
            try:
                cue.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise RetentionError("cue must be valid UTF-8") from exc
            count = item["count"]
            mean_milli = item["mean_milli"]
            first_tick = item["first_tick"]
            entry_last_tick = item["last_tick"]
            if (not _exact_int(count, 1, max_observations)
                    or not _exact_int(mean_milli, 0, 1000)
                    or not _exact_int(first_tick, 0, last_tick)
                    or not _exact_int(entry_last_tick, first_tick, last_tick)
                    or last_tick - entry_last_tick > retention_ticks):
                raise RetentionError("invalid cue aggregate")
            seen.add(cue)
            retained_observations += count
            normalized_entries.append({
                "cue": cue, "count": count, "mean_milli": mean_milli,
                "first_tick": first_tick, "last_tick": entry_last_tick,
            })

        if (last_tick == -1 and (total_admitted != 0 or normalized_entries)
                or total_admitted < retained_observations):
            raise RetentionError("inconsistent snapshot totals")

        # Hash only the newly built, bounded native structure. No caller object is
        # deep-copied, sorted, or serialized before every field has been admitted.
        unsigned = {
            "schema": SCHEMA, "last_tick": last_tick, "max_cues": max_cues,
            "retention_ticks": retention_ticks,
            "max_observations_per_cue": max_observations,
            "entries": normalized_entries, "total_admitted": total_admitted,
            "expired_cues": expired_cues, "capacity_evictions": capacity_evictions,
        }
        if _digest(unsigned) != digest:
            raise RetentionError("snapshot digest mismatch")

        ledger = cls(
            max_cues=max_cues, retention_ticks=retention_ticks,
            max_observations_per_cue=max_observations,
        )
        ledger.entries = {
            item["cue"]: {
                "count": item["count"], "mean_milli": item["mean_milli"],
                "first_tick": item["first_tick"], "last_tick": item["last_tick"],
            }
            for item in normalized_entries
        }
        ledger.last_tick = last_tick
        ledger.total_admitted = total_admitted
        ledger.expired_cues = expired_cues
        ledger.capacity_evictions = capacity_evictions
        return ledger
