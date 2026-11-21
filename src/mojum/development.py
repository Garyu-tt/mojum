"""Experimental GENESIS lifecycle fixture: evidence-gated maturation, no ageing.

Synthetic integer schedules, not empirical human brain curves. No neural backend,
clock-time ageing, memory deletion, simulated suffering, or journal integration.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

PROGRAM_SCHEMA = "mojum.genesis.development.program/1"
STATE_SCHEMA = "mojum.genesis.development.state/1"
SNAPSHOT_SCHEMA = "mojum.genesis.development.snapshot/1"
REGIONS = ("cortex", "cerebellum", "dentate_gyrus")
MAX_COUNTER = 2**63 - 1
REGION_KEYS = frozenset({
    "initial_immature", "initial_mature", "initial_synapses",
    "neuron_budget", "synapse_budget", "maturity_target",
    "maturation_per_experience", "neurogenesis_per_experience",
    "synaptogenesis_per_experience", "pruning_per_experience",
    "plasticity_milli",
})
STATE_REGION_KEYS = frozenset({
    "immature", "newborn", "mature", "synapses", "learning_events",
    "remodeling_events",
})


class DevelopmentError(ValueError):
    """Invalid lifecycle contract or impossible transition."""


def _keys(value, expected, label):
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise DevelopmentError(f"{label} requires exactly {sorted(expected)}")


def _integer(value, label, low=0, high=MAX_COUNTER):
    if type(value) is not int or not low <= value <= high:
        raise DevelopmentError(f"{label} must be integer within [{low}, {high}]")
    return value


def _identity(value, label):
    if type(value) is not str or not value.strip() or len(value) > 128:
        raise DevelopmentError(f"invalid {label}")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise DevelopmentError(f"invalid {label}: not UTF-8 encodable") from exc
    return value


def _digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise DevelopmentError("not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def synthetic_program():
    """Hypothetical small populations for reproducible tests, NOT human counts."""
    def region(immature, mature, budget, target, maturation, birth, synapses, syn_budget, grow, prune, plasticity):
        return {
            "initial_immature": immature, "initial_mature": mature,
            "initial_synapses": synapses, "neuron_budget": budget,
            "synapse_budget": syn_budget, "maturity_target": target,
            "maturation_per_experience": maturation,
            "neurogenesis_per_experience": birth,
            "synaptogenesis_per_experience": grow,
            "pruning_per_experience": prune,
            "plasticity_milli": plasticity,
        }
    return {
        "schema": PROGRAM_SCHEMA,
        "regions": {
            "cortex": region(12, 4, 32, 20, 2, 1, 16, 100, 4, 1, 850),
            "cerebellum": region(6, 3, 20, 12, 1, 2, 10, 70, 3, 2, 750),
            "dentate_gyrus": region(3, 2, 16, 8, 1, 2, 6, 50, 2, 0, 900),
        },
        "classification": "synthetic_hypothesis_not_human_calibration",
    }


def validate_program(program):
    _keys(program, {"schema", "regions", "classification"}, "program")
    if program["schema"] != PROGRAM_SCHEMA or program["classification"] != "synthetic_hypothesis_not_human_calibration":
        raise DevelopmentError("unsupported program version or classification")
    _keys(program["regions"], REGIONS, "regions")
    for name in REGIONS:
        r = program["regions"][name]
        _keys(r, REGION_KEYS, name)
        for key in REGION_KEYS:
            _integer(r[key], f"{name}.{key}")
        if r["neuron_budget"] <= 0 or r["synapse_budget"] <= 0:
            raise DevelopmentError("budgets must be positive")
        if r["initial_immature"] + r["initial_mature"] > r["neuron_budget"]:
            raise DevelopmentError("initial neurons exceed budget")
        if not r["initial_mature"] <= r["maturity_target"] <= r["neuron_budget"]:
            raise DevelopmentError("invalid maturity target")
        if r["initial_synapses"] > r["synapse_budget"]:
            raise DevelopmentError("initial synapses exceed budget")
        if not 1 <= r["plasticity_milli"] <= 1000:
            raise DevelopmentError("plasticity must remain positive and bounded")
    return copy.deepcopy(dict(program))


def initial_state(program, organism_id, genome_revision):
    checked = validate_program(program)
    _identity(organism_id, "organism_id")
    _identity(genome_revision, "genome_revision")
    return {
        "schema": STATE_SCHEMA, "organism_id": organism_id,
        "genome_revision": genome_revision, "elapsed_ticks": 0,
        "senescence_mode": "disabled", "learning_enabled": True,
        "regions": {
            name: {
                "immature": r["initial_immature"], "newborn": 0,
                "mature": r["initial_mature"], "synapses": r["initial_synapses"],
                "learning_events": 0, "remodeling_events": 0,
            }
            for name, r in checked["regions"].items()
        },
    }


def validate_state(state, program):
    checked = validate_program(program)
    _keys(state, {"schema", "organism_id", "genome_revision", "elapsed_ticks",
                  "senescence_mode", "learning_enabled", "regions"}, "state")
    if state["schema"] != STATE_SCHEMA or state["senescence_mode"] != "disabled" or state["learning_enabled"] is not True:
        raise DevelopmentError("unsupported lifecycle state or forbidden senescence")
    _identity(state["organism_id"], "organism_id")
    _identity(state["genome_revision"], "genome_revision")
    _integer(state["elapsed_ticks"], "elapsed_ticks")
    _keys(state["regions"], REGIONS, "state regions")
    for name in REGIONS:
        s = state["regions"][name]
        p = checked["regions"][name]
        _keys(s, STATE_REGION_KEYS, name)
        for key in STATE_REGION_KEYS:
            _integer(s[key], f"{name}.{key}")
        if s["immature"] + s["newborn"] + s["mature"] > p["neuron_budget"]:
            raise DevelopmentError("neuron budget exceeded")
        if s["synapses"] > p["synapse_budget"]:
            raise DevelopmentError("synapse budget exceeded")
        if s["learning_events"] > state["elapsed_ticks"] or s["remodeling_events"] > s["learning_events"]:
            raise DevelopmentError("impossible experience counters")
    return copy.deepcopy(dict(state))


def advance(state, program, evidence):
    """One logical tick; evidence is an exact per-region 0/1 exposure mask.

    Elapsed time alone never changes neurons, synapses, learning or plasticity.
    An exposed region can mature old immature cells; previous newborn cells
    become immature next tick, never mature immediately on their birth tick.
    """
    checked = validate_program(program)
    next_state = validate_state(state, checked)
    _keys(evidence, REGIONS, "evidence")
    for name in REGIONS:
        _integer(evidence[name], f"evidence.{name}", 0, 1)
    if next_state["elapsed_ticks"] == MAX_COUNTER:
        raise DevelopmentError("elapsed tick overflow")
    for name in REGIONS:
        if evidence[name] == 0:
            continue
        s, p = next_state["regions"][name], checked["regions"][name]
        if s["learning_events"] == MAX_COUNTER or s["remodeling_events"] == MAX_COUNTER:
            raise DevelopmentError("experience counter overflow")
        immature_before = s["immature"]
        newborn_before = s["newborn"]
        is_developing = s["mature"] < p["maturity_target"]
        moved = min(immature_before, p["maturation_per_experience"],
                    max(0, p["maturity_target"] - s["mature"]))
        s["mature"] += moved
        s["immature"] = immature_before - moved + newborn_before
        s["newborn"] = min(p["neurogenesis_per_experience"],
                           p["neuron_budget"] - s["immature"] - s["mature"])
        # Pruning changes connection counts during development, not neuron survival.
        growth = p["synaptogenesis_per_experience"]
        pruning = p["pruning_per_experience"] if is_developing else 0
        s["synapses"] = min(p["synapse_budget"], max(0, s["synapses"] + growth - pruning))
        s["learning_events"] += 1
        s["remodeling_events"] += 1  # exposure opportunities, not learned ability
    next_state["elapsed_ticks"] += 1
    return next_state


def metrics(state, program):
    """Read-only structural metrics; no claims about cognition or subjective age."""
    checked = validate_program(program)
    value = validate_state(state, checked)
    return {
        "organism_id": value["organism_id"],
        "elapsed_ticks": value["elapsed_ticks"],
        "senescence_mode": "disabled",
        "regions": {
            name: {
                **copy.deepcopy(value["regions"][name]),
                "maturity_milli": min(1000, 1000 * value["regions"][name]["mature"] // max(1, p["maturity_target"])),
                "plasticity_milli": p["plasticity_milli"],
                "neuron_budget": p["neuron_budget"],
            }
            for name, p in checked["regions"].items()
        },
    }


def snapshot(state, program):
    checked = validate_program(program)
    value = validate_state(state, checked)
    payload = {"schema": SNAPSHOT_SCHEMA, "program_sha256": _digest(checked), "state": value}
    return {**payload, "checksum_sha256": _digest(payload)}


def restore(snapshot_value, program):
    checked = validate_program(program)
    _keys(snapshot_value, {"schema", "program_sha256", "state", "checksum_sha256"}, "snapshot")
    if snapshot_value["schema"] != SNAPSHOT_SCHEMA or snapshot_value["program_sha256"] != _digest(checked):
        raise DevelopmentError("snapshot program mismatch")
    payload = {key: snapshot_value[key] for key in ("schema", "program_sha256", "state")}
    if type(snapshot_value["checksum_sha256"]) is not str or snapshot_value["checksum_sha256"] != _digest(payload):
        raise DevelopmentError("snapshot checksum mismatch (not authentication)")
    return validate_state(snapshot_value["state"], checked)
