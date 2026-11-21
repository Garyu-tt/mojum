"""Synthetic per-region developmental clock. No brain, biology, or senescence.

This optional R0.1 fixture has no effect on Aquarium until explicitly wired.
It counts observed signals, not learned skills or synapses. The fixed schema
never deletes neurons or reduces capacity as a function of chronological age.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

SCHEMA = "mojum.lifecycle.synthetic-populations/1"
MAX_COUNTER = 2**63 - 1
MAX_SIGNALS_PER_TICK = 10_000
REGIONS = ("cortex", "cerebellum", "dentate_gyrus")
FIELDS = ("initial_mature", "initial_immature", "target_mature",
          "birth_per_tick", "mature_per_tick", "signals_per_maturation")
DEFAULT_PROFILE = {
    "cortex": dict(zip(FIELDS, (2, 2, 14, 2, 2, 3))),
    "cerebellum": dict(zip(FIELDS, (4, 1, 12, 1, 2, 2))),
    "dentate_gyrus": dict(zip(FIELDS, (1, 0, 9, 1, 1, 4))),
}
STATE_FIELDS = ("mature", "immature", "evidence_credit", "observations_total",
                "post_maturity_observations")


class LifecycleError(ValueError):
    """Invalid profile, state, or transition; no mutation on failure."""


def _integer(value, label, lo=0, hi=MAX_COUNTER):
    if type(value) is not int or not lo <= value <= hi:
        raise LifecycleError(f"{label} must be an integer in [{lo}, {hi}]")
    return value


def _digest(value):
    try:
        wire = json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LifecycleError("noncanonical lifecycle snapshot") from exc
    return hashlib.sha256(wire.encode("ascii")).hexdigest()


def _profile(value):
    if type(value) is not dict or set(value) != set(REGIONS):
        raise LifecycleError("profile must define exactly three named regions")
    for region in REGIONS:
        p = value[region]
        if type(p) is not dict or set(p) != set(FIELDS):
            raise LifecycleError(f"invalid {region} parameters")
        for field in FIELDS:
            _integer(p[field], f"{region}.{field}",
                     0 if field in ("initial_mature", "initial_immature") else 1,
                     1_000_000)
        if p["initial_mature"] + p["initial_immature"] > p["target_mature"]:
            raise LifecycleError(f"{region} initial population exceeds target")
    return copy.deepcopy(value)


class DevelopmentClock:
    """Bounded structural growth and independent lifelong observation counters.

    The caller provides per-region *synthetic* evidence on each discrete tick.
    Growth stops at the configured target, but observations continue counting.
    This is not a trained neuronal network or a model of subjective experience.
    """

    def __init__(self, organism_id, genome_revision, *, profile=None):
        if (type(organism_id) is not str or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", organism_id)):
            raise LifecycleError("invalid stable organism identity")
        if (type(genome_revision) is not str or
                not re.fullmatch(r"[0-9a-f]{64}", genome_revision)):
            raise LifecycleError("genome_revision must be a SHA-256 digest")
        self.organism_id = organism_id
        self.genome_revision = genome_revision
        self.profile = _profile(DEFAULT_PROFILE if profile is None else profile)
        self.tick = 0
        self.regions = {
            name: {
                "mature": self.profile[name]["initial_mature"],
                "immature": self.profile[name]["initial_immature"],
                "evidence_credit": 0,
                "observations_total": 0,
                "post_maturity_observations": 0,
            }
            for name in REGIONS
        }

    def advance(self, signals):
        """One atomic tick; reject invalid/overflowing evidence before mutation."""
        if type(signals) is not dict or set(signals) != set(REGIONS):
            raise LifecycleError("each region needs a synthetic signal count")
        if self.tick >= MAX_COUNTER:
            raise LifecycleError("elapsed clock overflow")
        for region in REGIONS:
            _integer(signals[region], f"signals.{region}", 0,
                     MAX_SIGNALS_PER_TICK)
        next_regions = copy.deepcopy(self.regions)
        for region in REGIONS:
            p = self.profile[region]
            s = next_regions[region]
            count = signals[region]
            if s["observations_total"] > MAX_COUNTER - count:
                raise LifecycleError("observation counter overflow")
            was_mature = s["mature"] == p["target_mature"]
            if was_mature and s["post_maturity_observations"] > MAX_COUNTER - count:
                raise LifecycleError("post-maturity counter overflow")
            s["observations_total"] += count
            if was_mature:
                s["post_maturity_observations"] += count
            total = s["mature"] + s["immature"]
            births = min(p["birth_per_tick"], p["target_mature"] - total)
            s["immature"] += births
            # Credit is bounded even if evidence arrives long before births.
            credit_cap = p["target_mature"] * p["signals_per_maturation"]
            s["evidence_credit"] = min(credit_cap, s["evidence_credit"] + count)
            matured = min(s["immature"], p["mature_per_tick"],
                          s["evidence_credit"] // p["signals_per_maturation"])
            s["immature"] -= matured
            s["mature"] += matured
            s["evidence_credit"] -= matured * p["signals_per_maturation"]
            if s["mature"] == p["target_mature"]:
                s["evidence_credit"] = 0
        self.regions = next_regions
        self.tick += 1
        return self.snapshot()

    def snapshot(self):
        record = {
            "schema": SCHEMA,
            "organism_id": self.organism_id,
            "genome_revision": self.genome_revision,
            "tick": self.tick,
            "profile": copy.deepcopy(self.profile),
            "regions": copy.deepcopy(self.regions),
            "trust": "checksum_only_not_authenticated",
        }
        return {**record, "sha256": _digest(record)}

    @classmethod
    def from_snapshot(cls, snapshot):
        required = {"schema", "organism_id", "genome_revision", "tick",
                    "profile", "regions", "trust", "sha256"}
        if type(snapshot) is not dict or set(snapshot) != required:
            raise LifecycleError("invalid lifecycle snapshot shape")
        if snapshot["schema"] != SCHEMA or snapshot["trust"] != "checksum_only_not_authenticated":
            raise LifecycleError("unsupported lifecycle schema or trust claim")
        if (type(snapshot["sha256"]) is not str or
                not re.fullmatch(r"[0-9a-f]{64}", snapshot["sha256"])):
            raise LifecycleError("invalid snapshot checksum")
        record = {key: value for key, value in snapshot.items() if key != "sha256"}
        if _digest(record) != snapshot["sha256"]:
            raise LifecycleError("changed lifecycle snapshot")
        _integer(snapshot["tick"], "tick")
        inst = cls(snapshot["organism_id"], snapshot["genome_revision"],
                   profile=snapshot["profile"])
        regions = snapshot["regions"]
        if type(regions) is not dict or set(regions) != set(REGIONS):
            raise LifecycleError("invalid regions in snapshot")
        for region in REGIONS:
            p = inst.profile[region]
            s = regions[region]
            if type(s) is not dict or set(s) != set(STATE_FIELDS):
                raise LifecycleError(f"invalid {region} state")
            for field in STATE_FIELDS:
                _integer(s[field], f"{region}.{field}")
            if (not p["initial_mature"] <= s["mature"] <= p["target_mature"]
                    or not p["initial_mature"] + p["initial_immature"]
                    <= s["mature"] + s["immature"] <= p["target_mature"]
                    or s["mature"] > p["initial_mature"] + snapshot["tick"] * p["mature_per_tick"]
                    or s["mature"] + s["immature"] > p["initial_mature"] + p["initial_immature"] + snapshot["tick"] * p["birth_per_tick"]
                    or s["observations_total"] > snapshot["tick"] * MAX_SIGNALS_PER_TICK
                    or s["evidence_credit"] > s["observations_total"]
                    or s["evidence_credit"] > p["target_mature"] * p["signals_per_maturation"]
                    or s["post_maturity_observations"] > s["observations_total"]
                    or (s["mature"] < p["target_mature"] and
                        s["post_maturity_observations"] != 0)
                    or (s["mature"] == p["target_mature"] and
                        (s["immature"] != 0 or s["evidence_credit"] != 0))):
                raise LifecycleError(f"inconsistent {region} population")
        inst.tick = snapshot["tick"]
        inst.regions = copy.deepcopy(regions)
        return inst
