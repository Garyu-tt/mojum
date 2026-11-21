"""Deterministic, opt-in GENESIS maturation fixture; not a neural simulator.

Structural maturation requires explicit synthetic evidence. Virtual time alone
never kills, ages out, prunes or freezes an organism. No food/water dependency.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

PROFILE_SCHEMA = "mojum.lifecycle.profile/1"
SNAPSHOT_SCHEMA = "mojum.lifecycle.snapshot/1"
MAX_TICKS = 2**63 - 1
MAX_EVENTS = 4096
MAX_UNITS = 10_000_000
_REGION = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_FIELDS = {
    "initial_mature", "initial_immature", "newborn_cap", "birth_per_evidence",
    "mature_per_evidence", "synapses_initial", "synapses_cap",
    "synapses_per_learning", "prune_per_evidence", "prune_cap",
}


class LifecycleError(ValueError):
    """Invalid versioned fixture, event, or replay."""


def _integer(value, field, limit):
    if type(value) is not int or not 0 <= value <= limit:
        raise LifecycleError(f"{field} must be an integer within 0..{limit}")
    return value


def _keys(value, names, subject):
    if type(value) is not dict or set(value) != names:
        raise LifecycleError(f"invalid {subject} keys")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def validate_profile(profile):
    _keys(profile, {"schema", "organism_id", "senescence_enabled", "regions"}, "profile")
    if profile["schema"] != PROFILE_SCHEMA:
        raise LifecycleError("unsupported lifecycle profile schema")
    name = profile["organism_id"]
    if type(name) is not str or not 1 <= len(name) <= 128 or not name.strip():
        raise LifecycleError("invalid immutable organism ID")
    # JSON can decode lone UTF-16 surrogates, but UTF-8 hashing cannot encode
    # them. Reject at the untrusted boundary instead of crashing at revision.
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LifecycleError("organism ID contains invalid Unicode") from exc
    # This fixture deliberately implements no senescence; a future experimental
    # policy must use an independently versioned schema, never silent migration.
    if profile["senescence_enabled"] is not False:
        raise LifecycleError("senescence is not supported by this profile")
    regions = profile["regions"]
    if type(regions) is not dict or not 1 <= len(regions) <= 16:
        raise LifecycleError("expected 1..16 named regions")
    for region, cfg in regions.items():
        if type(region) is not str or not _REGION.fullmatch(region):
            raise LifecycleError("invalid region ID")
        _keys(cfg, _FIELDS, f"region {region}")
        for field, number in cfg.items():
            _integer(number, field, 1_000_000)
        if cfg["synapses_initial"] > cfg["synapses_cap"]:
            raise LifecycleError("initial synapses exceed capacity")
        if cfg["prune_cap"] > cfg["synapses_initial"]:
            raise LifecycleError("pruning exceeds initial synapses")
    return copy.deepcopy(profile)


class Lifecycle:
    """Bounded event ledger with independent elapsed/development/learning clocks.

    Checksums detect accidental changes but do not authenticate an owner.
    Callers must persist snapshots privately and supply independent custody.
    """

    def __init__(self, profile):
        self._profile = validate_profile(profile)
        self._tick = 0
        self._events = []
        self._counters = {region: {"development": 0, "learning": 0}
                          for region in self._profile["regions"]}

    @property
    def profile(self):
        """Return a copy; the organism identity and structural genes are immutable."""
        return copy.deepcopy(self._profile)

    @property
    def tick(self):
        return self._tick

    @property
    def revision(self):
        return _hash({"profile": self._profile, "events": self._events})

    def _check_cas(self, expected_revision):
        if type(expected_revision) is not str or expected_revision != self.revision:
            raise LifecycleError("stale lifecycle revision (CAS failed)")
        if len(self._events) >= MAX_EVENTS:
            raise LifecycleError("lifecycle event journal full")

    def advance(self, ticks, *, expected_revision):
        """Advance virtual time in O(1), without structural age side effects."""
        self._check_cas(expected_revision)
        _integer(ticks, "ticks", MAX_TICKS)
        if ticks == 0 or self._tick > MAX_TICKS - ticks:
            raise LifecycleError("invalid or overflowing tick increment")
        self._events.append({"kind": "advance", "ticks": ticks})
        self._tick += ticks
        return self.revision

    def experience(self, region, *, development_units=0, learning_units=0,
                   expected_revision):
        """Record voluntary synthetic evidence and learning practice, separately."""
        self._check_cas(expected_revision)
        if type(region) is not str or region not in self._counters:
            raise LifecycleError("unknown region")
        _integer(development_units, "development_units", MAX_UNITS)
        _integer(learning_units, "learning_units", MAX_UNITS)
        if not (development_units or learning_units):
            raise LifecycleError("empty experience is not an event")
        counts = self._counters[region]
        if any(counts[k] > MAX_UNITS - inc for k, inc in
               (("development", development_units), ("learning", learning_units))):
            raise LifecycleError("evidence capacity exceeded")
        event = {"kind": "experience", "at_tick": self._tick, "region": region,
                 "development_units": development_units,
                 "learning_units": learning_units}
        self._events.append(event)
        counts["development"] += development_units
        counts["learning"] += learning_units
        return self.revision

    def region(self, name):
        if type(name) is not str or name not in self._counters:
            raise LifecycleError("unknown region")
        cfg = self._profile["regions"][name]
        counts = self._counters[name]
        development = counts["development"]
        learning = counts["learning"]
        newborn = min(cfg["newborn_cap"], development * cfg["birth_per_evidence"])
        matured = min(cfg["initial_immature"] + newborn,
                      development * cfg["mature_per_evidence"])
        pruned = min(cfg["prune_cap"], development * cfg["prune_per_evidence"])
        synapses_added = min(cfg["synapses_cap"] - cfg["synapses_initial"],
                             learning * cfg["synapses_per_learning"])
        return {
            "region": name, "initial_mature": cfg["initial_mature"],
            "mature_neurons": cfg["initial_mature"] + matured,
            "immature_neurons": cfg["initial_immature"] + newborn - matured,
            "newborn_created": newborn,
            "developmental_synapses_pruned": pruned,
            "synapses": cfg["synapses_initial"] - pruned + synapses_added,
            "development_progress": development,
            "learning_progress": learning,
            "structurally_mature": (matured == cfg["initial_immature"] + cfg["newborn_cap"]),
            "source_classification": "synthetic_fixture_not_biological_calibration",
        }

    def snapshot(self):
        return {"schema": SNAPSHOT_SCHEMA, "profile": copy.deepcopy(self._profile),
                "tick": self._tick, "events": copy.deepcopy(self._events),
                "revision": self.revision,
                "trust": "checksum_only_not_authenticated"}

    @classmethod
    def from_snapshot(cls, snapshot):
        _keys(snapshot, {"schema", "profile", "tick", "events", "revision", "trust"}, "snapshot")
        if snapshot["schema"] != SNAPSHOT_SCHEMA:
            raise LifecycleError("unsupported lifecycle snapshot")
        if snapshot["trust"] != "checksum_only_not_authenticated":
            raise LifecycleError("unsupported trust annotation")
        _integer(snapshot["tick"], "snapshot tick", MAX_TICKS)
        events = snapshot["events"]
        if type(events) is not list or len(events) > MAX_EVENTS:
            raise LifecycleError("invalid event list")
        restored = cls(snapshot["profile"])
        for e in events:
            if type(e) is not dict:
                raise LifecycleError("invalid lifecycle event")
            if e.get("kind") == "advance":
                _keys(e, {"kind", "ticks"}, "advance event")
                restored.advance(e["ticks"], expected_revision=restored.revision)
            elif e.get("kind") == "experience":
                _keys(e, {"kind", "at_tick", "region", "development_units", "learning_units"},
                      "experience event")
                if e["at_tick"] != restored.tick:
                    raise LifecycleError("experience tick mismatch")
                restored.experience(e["region"], development_units=e["development_units"],
                                    learning_units=e["learning_units"],
                                    expected_revision=restored.revision)
            else:
                raise LifecycleError("unknown lifecycle event kind")
        if restored.tick != snapshot["tick"] or restored.revision != snapshot["revision"]:
            raise LifecycleError("lifecycle snapshot checksum or tick mismatch")
        return restored
