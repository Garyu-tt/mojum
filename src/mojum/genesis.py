"""Deterministic GENESIS genotype -> fixture phenotype contracts.

No neural training, evolution, biological claim, persistent memory or actuation.
Integer-only parameters and an append-only in-memory acquisition ledger.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

GENOTYPE_SCHEMA = "mojum.genesis.genotype/1"
PHENOTYPE_SCHEMA = "mojum.genesis.phenotype/1"
ACQUISITION_SCHEMA = "mojum.genesis.acquisition/1"
GENES = {
    "heartbeat_bpm": (30, 180),
    "body_scale_milli": (500, 1500),
    "sensory_gain_milli": (100, 2000),
}


class GenesisError(ValueError):
    """Rejected untrusted genome or deterministic-state transition."""


def _int(value, path, low=0, high=2**63 - 1):
    if type(value) is not int or not low <= value <= high:
        raise GenesisError(f"{path} must be an integer in [{low}, {high}]")
    return value


def _str(value, path, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise GenesisError(f"{path} must be a nonempty string (<= {limit})")
    # JSON can spell lone Unicode surrogates, but they are not encodable as
    # UTF-8 for stable revision hashes. Reject at the contract boundary.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GenesisError(f"{path} must be valid UTF-8") from exc
    return value


def _keys(value, required, path):
    if not isinstance(value, Mapping) or set(value) != set(required):
        raise GenesisError(f"{path} must have exactly {sorted(required)}")


def canonical(value):
    """Stable serialization; rejects NaN, infinity, unknown object types."""
    try:
        serialized = json.dumps(
            value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        )
        # ensure_ascii=False preserves Unicode; an unpaired surrogate would
        # otherwise escape validation until sha256() calls .encode().
        serialized.encode("utf-8")
        # JSON silently coerces integer object keys to strings and tuples to
        # arrays. Those changes must not receive the original object's hash.
        if json.loads(serialized) != value:
            raise GenesisError("Genome is not lossless canonical JSON")
        return serialized
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise GenesisError("Genome is not canonical UTF-8 JSON") from exc


def sha256(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_seed(seed):
    _keys(seed, {"schema", "genome_id", "revision", "inherited"}, "seed")
    if seed["schema"] != GENOTYPE_SCHEMA:
        raise GenesisError("Unsupported genotype schema")
    _str(seed["genome_id"], "genome_id")
    _str(seed["revision"], "revision")
    _keys(seed["inherited"], GENES, "inherited")
    for name, (lo, hi) in GENES.items():
        _int(seed["inherited"][name], f"inherited.{name}", lo, hi)
    return copy.deepcopy(dict(seed))


def validate_acquisition(acquisition, genome_id):
    required = {
        "schema", "event_id", "genome_id", "gene", "delta",
        "valid_from_tick", "observed_at_ns", "recorded_at_ns",
        "source", "evidence",
    }
    _keys(acquisition, required, "acquisition")
    if acquisition["schema"] != ACQUISITION_SCHEMA:
        raise GenesisError("Unsupported acquisition schema")
    if acquisition["genome_id"] != genome_id:
        raise GenesisError("Acquisition belongs to another genome")
    _str(acquisition["event_id"], "event_id")
    if not isinstance(acquisition["gene"], str) or acquisition["gene"] not in GENES:
        raise GenesisError("Unknown gene")
    _int(acquisition["delta"], "delta", -2_000, 2_000)
    _int(acquisition["valid_from_tick"], "valid_from_tick")
    _int(acquisition["observed_at_ns"], "observed_at_ns")
    _int(acquisition["recorded_at_ns"], "recorded_at_ns")
    if acquisition["recorded_at_ns"] < acquisition["observed_at_ns"]:
        raise GenesisError("recorded_at_ns predates observed_at_ns")
    _str(acquisition["source"], "source", 256)
    _str(acquisition["evidence"], "evidence", 512)
    return copy.deepcopy(dict(acquisition))


class GenomeLedger:
    """Append-only event fixture with optimistic compare-and-swap revision.

    Revisions are content hashes, not authentication signatures; persistence and
    replication belong to future Noosphere adapters.
    """

    MAX_EVENTS = 1024

    def __init__(self, seed):
        self._seed = validate_seed(seed)
        self._events = []
        self._event_ids = set()

    @property
    def revision(self):
        return sha256({"seed": self._seed, "events": self._events})

    def snapshot(self):
        value = {
            "schema": "mojum.genesis.ledger/1",
            "seed": copy.deepcopy(self._seed),
            "events": copy.deepcopy(self._events),
            "revision": self.revision,
        }
        return value

    def append(self, event, expected_revision):
        if type(expected_revision) is not str or expected_revision != self.revision:
            raise GenesisError("Stale or absent ledger revision (CAS failed)")
        checked = validate_acquisition(event, self._seed["genome_id"])
        if len(self._events) >= self.MAX_EVENTS:
            raise GenesisError("Ledger event count limit exceeded")
        if checked["event_id"] in self._event_ids:
            raise GenesisError("Duplicate acquisition event ID")
        if self._events and checked["recorded_at_ns"] < self._events[-1]["recorded_at_ns"]:
            raise GenesisError("recorded_at_ns must be monotonic")
        # Reject out-of-range projections before accepting an append-only event.
        next_events = self._events + [checked]
        self._check_all_effective_prefixes(next_events)
        self._events.append(checked)
        self._event_ids.add(checked["event_id"])
        return self.revision

    def _check_all_effective_prefixes(self, events):
        # At every recorded valid_from_tick boundary, all simultaneously active
        # deltas must produce valid parameters. This prevents future overflow.
        # Group only temporary references: acquisition order still defines the
        # ledger revision. Apply a whole boundary before testing its bounds.
        at_tick = {0: []}
        for event in events:
            at_tick.setdefault(event["valid_from_tick"], []).append(event)
        values = dict(self._seed["inherited"])
        for tick in sorted(at_tick):
            for event in at_tick[tick]:
                values[event["gene"]] += event["delta"]
            for name, (lo, hi) in GENES.items():
                if not lo <= values[name] <= hi:
                    raise GenesisError(f"Acquired genome exceeds {name} bounds at tick {tick}")

    def phenotype(self, tick):
        _int(tick, "tick")
        params = dict(self._seed["inherited"])
        selected = [event for event in self._events if event["valid_from_tick"] <= tick]
        for event in selected:
            params[event["gene"]] += event["delta"]
        # Deterministic by append order; provenance remains per event.
        return {
            "schema": PHENOTYPE_SCHEMA,
            "genome_id": self._seed["genome_id"],
            "at_tick": tick,
            "inherited_seed_sha256": sha256(self._seed),
            "ledger_revision": self.revision,
            "parameters": params,
            "applied_acquisition_ids": [event["event_id"] for event in selected],
            "source_classification": "synthetic_fixture_not_anatomy_or_neural_model",
        }

    @classmethod
    def from_snapshot(cls, snapshot):
        _keys(snapshot, {"schema", "seed", "events", "revision"}, "ledger snapshot")
        if snapshot["schema"] != "mojum.genesis.ledger/1":
            raise GenesisError("Unsupported ledger snapshot schema")
        if not isinstance(snapshot["events"], list) or len(snapshot["events"]) > cls.MAX_EVENTS:
            raise GenesisError("Snapshot events must be bounded list")
        ledger = cls(snapshot["seed"])
        for event in snapshot["events"]:
            ledger.append(event, ledger.revision)
        if snapshot["revision"] != ledger.revision:
            raise GenesisError("Ledger content checksum mismatch")
        return ledger
