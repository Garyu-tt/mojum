"""Bounded, deterministic LOD allocation for synthetic neural populations.

This module selects caller-declared representation tiers under an integer
resource budget. It is an advisory allocator, not a neural simulator or a
claim that coarse and fine representations are biologically equivalent.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Final

SCHEMA: Final = "mojum.neural.population-lod/1"
CLASSIFICATION: Final = "PUBLIC_SYNTHETIC"
MAX_POPULATIONS: Final = 128
MAX_BUDGET_UNITS: Final = 1_000_000
MAX_COST_UNITS: Final = 100_000
MAX_IMPORTANCE_MILLI: Final = 1_000_000
MAX_ID_CHARS: Final = 64
_LEVELS: Final = ("coarse", "medium", "fine")
_ROW_KEYS: Final = frozenset({
    "population_id", "importance_milli",
    "coarse_cost_units", "medium_cost_units", "fine_cost_units",
})


class NeuralLODError(ValueError):
    """Invalid population descriptors or resource budget."""


@dataclass(frozen=True, slots=True)
class PopulationLOD:
    """One population's selected caller-defined representation tier."""

    population_id: str
    level: str
    cost_units: int
    importance_milli: int


@dataclass(frozen=True, slots=True)
class LODPlan:
    """Immutable allocation receipt; digest is consistency metadata only."""

    schema: str
    classification: str
    budget_units: int
    baseline_cost_units: int
    used_cost_units: int
    remaining_cost_units: int
    upgrades: int
    populations: tuple[PopulationLOD, ...]
    sha256: str


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise NeuralLODError(f"{name} must be a native integer in [{low}, {high}]")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_ID_CHARS:
        raise NeuralLODError(f"population_id must contain 1..{MAX_ID_CHARS} characters")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    if value[0] not in allowed[:62] or any(char not in allowed for char in value):
        raise NeuralLODError("population_id must be a restricted ASCII identifier")
    return value


def _canonical_payload(
    budget: int,
    baseline: int,
    used: int,
    upgrades: int,
    populations: tuple[PopulationLOD, ...],
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "classification": CLASSIFICATION,
        "budget_units": budget,
        "baseline_cost_units": baseline,
        "used_cost_units": used,
        "remaining_cost_units": budget - used,
        "upgrades": upgrades,
        "populations": [
            {
                "population_id": item.population_id,
                "level": item.level,
                "cost_units": item.cost_units,
                "importance_milli": item.importance_milli,
            }
            for item in populations
        ],
    }


def plan_lod(
    populations: object,
    budget_units: object,
    *,
    classification: object,
    consent: object,
) -> LODPlan:
    """Allocate coarse baselines, then greedily buy the best affordable upgrades.

    Each upgrade has equal caller-supplied importance gain and its own
    incremental cost. Ratios are compared by integer cross multiplication;
    ties resolve by population ID, then by tier. This bounded greedy policy is
    deterministic but is not a globally optimal knapsack solver.
    """
    if type(classification) is not str or classification != CLASSIFICATION:
        raise NeuralLODError("input must be explicitly PUBLIC_SYNTHETIC")
    if type(consent) is not bool or consent is not True:
        raise NeuralLODError("explicit consent=True is required")
    budget = _integer(budget_units, "budget_units", 1, MAX_BUDGET_UNITS)
    if type(populations) is not list or not 1 <= len(populations) <= MAX_POPULATIONS:
        raise NeuralLODError(f"populations must be a native list of 1..{MAX_POPULATIONS} rows")

    rows: list[tuple[str, int, tuple[int, int, int]]] = []
    seen: set[str] = set()
    for index, row in enumerate(populations):
        if type(row) is not dict or len(row) != len(_ROW_KEYS):
            raise NeuralLODError(f"population row {index} must be an exact native dict")
        if any(type(key) is not str for key in row) or set(row) != _ROW_KEYS:
            raise NeuralLODError(f"population row {index} has unknown or non-native keys")
        population_id = _identifier(row["population_id"])
        if population_id in seen:
            raise NeuralLODError("population IDs must be unique")
        seen.add(population_id)
        importance = _integer(
            row["importance_milli"], "importance_milli", 0, MAX_IMPORTANCE_MILLI
        )
        costs = (
            _integer(row["coarse_cost_units"], "coarse_cost_units", 1, MAX_COST_UNITS),
            _integer(row["medium_cost_units"], "medium_cost_units", 1, MAX_COST_UNITS),
            _integer(row["fine_cost_units"], "fine_cost_units", 1, MAX_COST_UNITS),
        )
        if not costs[0] < costs[1] < costs[2]:
            raise NeuralLODError("representation costs must strictly increase coarse < medium < fine")
        rows.append((population_id, importance, costs))

    rows.sort(key=lambda item: item[0])
    baseline = sum(costs[0] for _, _, costs in rows)
    if baseline > budget:
        raise NeuralLODError("budget cannot fund the coarse baseline for every population")

    # State stores the chosen tier index for each sorted population.
    tiers = [0] * len(rows)
    used = baseline
    upgrades = 0

    # At most two upgrades per population. The fixed 128-row limit bounds
    # this O(populations * upgrades) selection loop.
    while True:
        best_index: int | None = None
        best_gain = 0
        best_cost = 1
        best_tier = 0
        for index, (population_id, importance, costs) in enumerate(rows):
            tier = tiers[index]
            if tier >= 2:
                continue
            incremental = costs[tier + 1] - costs[tier]
            if used + incremental > budget:
                continue
            gain = importance
            if gain == 0:
                continue
            if best_index is None:
                best_index, best_gain, best_cost, best_tier = index, gain, incremental, tier
                continue
            # Higher gain/cost wins. Equal ratios resolve lexically by ID,
            # then by lower next tier (coarse->medium before medium->fine).
            left = gain * best_cost
            right = best_gain * incremental
            best_id = rows[best_index][0]
            if left > right or (
                left == right
                and (population_id, tier) < (best_id, best_tier)
            ):
                best_index, best_gain, best_cost, best_tier = index, gain, incremental, tier
        if best_index is None:
            break
        tiers[best_index] += 1
        used += best_cost
        upgrades += 1

    selected = tuple(
        PopulationLOD(
            population_id=population_id,
            level=_LEVELS[tiers[index]],
            cost_units=costs[tiers[index]],
            importance_milli=importance,
        )
        for index, (population_id, importance, costs) in enumerate(rows)
    )
    payload = _canonical_payload(budget, baseline, used, upgrades, selected)
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return LODPlan(
        schema=SCHEMA,
        classification=CLASSIFICATION,
        budget_units=budget,
        baseline_cost_units=baseline,
        used_cost_units=used,
        remaining_cost_units=budget - used,
        upgrades=upgrades,
        populations=selected,
        sha256=digest,
    )


def verify_plan(plan: object) -> bool:
    """Recompute a plan's bounded consistency digest and accounting invariants."""
    if type(plan) is not LODPlan:
        raise NeuralLODError("an exact LODPlan is required")
    if (
        type(plan.schema) is not str or plan.schema != SCHEMA
        or type(plan.classification) is not str or plan.classification != CLASSIFICATION
        or type(plan.populations) is not tuple
        or not 1 <= len(plan.populations) <= MAX_POPULATIONS
    ):
        raise NeuralLODError("invalid LOD plan envelope")
    budget = _integer(plan.budget_units, "budget_units", 1, MAX_BUDGET_UNITS)
    baseline = _integer(plan.baseline_cost_units, "baseline_cost_units", 1, MAX_POPULATIONS * MAX_COST_UNITS)
    used = _integer(plan.used_cost_units, "used_cost_units", baseline, budget)
    remaining = _integer(plan.remaining_cost_units, "remaining_cost_units", 0, MAX_BUDGET_UNITS)
    upgrades = _integer(plan.upgrades, "upgrades", 0, 2 * MAX_POPULATIONS)
    if remaining != budget - used:
        raise NeuralLODError("LOD plan accounting mismatch")
    prior = ""
    computed_baseline = 0
    computed_used = 0
    for item in plan.populations:
        if type(item) is not PopulationLOD:
            raise NeuralLODError("LOD plan contains a noncanonical population")
        population_id = _identifier(item.population_id)
        if population_id <= prior:
            raise NeuralLODError("LOD plan populations must be unique and sorted")
        prior = population_id
        if type(item.level) is not str or item.level not in _LEVELS:
            raise NeuralLODError("unsupported LOD tier")
        cost = _integer(item.cost_units, "cost_units", 1, MAX_COST_UNITS)
        _integer(item.importance_milli, "importance_milli", 0, MAX_IMPORTANCE_MILLI)
        computed_used += cost
        # Every tier costs at least one unit; the baseline cannot be recovered
        # from a selected tier without its original descriptor, so only check
        # accounting and digest here.
    if computed_used != used or upgrades > 2 * len(plan.populations):
        raise NeuralLODError("LOD plan accounting mismatch")
    payload = _canonical_payload(
        budget, baseline, used, upgrades, plan.populations
    )
    expected = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    if type(plan.sha256) is not str or plan.sha256 != expected:
        raise NeuralLODError("LOD plan digest mismatch")
    return True
