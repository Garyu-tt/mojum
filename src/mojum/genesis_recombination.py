"""Deterministic two-parent recombination for synthetic GENESIS genotype fixtures.

This is an explicit inheritance operator, not a biological meiosis model.
"""
from __future__ import annotations

import copy
import hashlib

from .genesis import GENES, GENOTYPE_SCHEMA, GenesisError, canonical, validate_seed

RECOMBINATION_SCHEMA = "mojum.genesis.recombination/1"
_PROVENANCE_SCHEMA = "mojum.genesis.recombination-provenance/1"
_MAX_CROSSOVERS = max(0, len(GENES) - 2)


class RecombinationError(ValueError):
    """Invalid parent, crossover map, or recombination request."""


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value or not value.strip() or len(value) > 128:
        raise RecombinationError(f"{field} must be a nonempty native string <= 128 chars")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecombinationError(f"{field} must be valid UTF-8") from exc
    return value


def _checked_parent(value: object, field: str) -> dict:
    # Require ordinary dicts before calling the shared GENESIS validator so
    # hostile Mapping implementations cannot execute arbitrary methods here.
    if (type(value) is not dict
            or any(type(key) is not str for key in value)
            or type(value.get("inherited")) is not dict
            or any(type(key) is not str for key in value["inherited"])):
        raise RecombinationError(f"{field} must be a native GENESIS seed object")
    try:
        return validate_seed(value)
    except (GenesisError, TypeError, ValueError, RecursionError) as exc:
        raise RecombinationError(f"{field} is not a valid GENESIS seed") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def recombine(
    parent_a: dict,
    parent_b: dict,
    *,
    child_genome_id: str,
    starting_parent: int = 0,
    crossover_after: tuple[int, ...] | list[int] = (),
) -> dict:
    """Create a deterministic child seed and detached inheritance provenance.

    crossover_after contains zero-based gene indices after which the
    contributing parent toggles. Genes use canonical insertion order from
    mojum.genesis.GENES. For example, (1,) means genes 0..1 come from the
    starting parent and genes 2..end come from the other parent.
    No random source or implicit mutation is used.
    """
    a = _checked_parent(parent_a, "parent_a")
    b = _checked_parent(parent_b, "parent_b")
    if a["genome_id"] == b["genome_id"]:
        raise RecombinationError("parents must have distinct genome_id values")
    child_id = _identifier(child_genome_id, "child_genome_id")
    if child_id in (a["genome_id"], b["genome_id"]):
        raise RecombinationError("child_genome_id must be new and distinct from both parents")
    if type(starting_parent) is not int or starting_parent not in (0, 1):
        raise RecombinationError("starting_parent must be native integer 0 or 1")
    if type(crossover_after) not in (tuple, list):
        raise RecombinationError("crossover_after must be a native tuple or list")
    if len(crossover_after) > _MAX_CROSSOVERS:
        raise RecombinationError("too many crossover boundaries")

    gene_names = tuple(GENES)
    previous = 0
    for boundary in crossover_after:
        if type(boundary) is not int or not 0 < boundary < len(gene_names) - 1:
            raise RecombinationError("crossover boundary must be a native interior gene index")
        if boundary <= previous:
            raise RecombinationError("crossover boundaries must be strictly increasing and unique")
        previous = boundary

    boundaries = frozenset(crossover_after)
    active_parent = starting_parent
    inherited: dict[str, int] = {}
    inherited_from: dict[str, str] = {}
    parents = (a, b)
    for index, gene in enumerate(gene_names):
        if index > 0 and index - 1 in boundaries:
            active_parent = 1 - active_parent
        inherited[gene] = parents[active_parent]["inherited"][gene]
        inherited_from[gene] = parents[active_parent]["genome_id"]

    parent_hashes = [_digest(a), _digest(b)]
    provenance_body = {
        "schema": _PROVENANCE_SCHEMA,
        "child_genome_id": child_id,
        "parent_genome_ids": [a["genome_id"], b["genome_id"]],
        "parent_seed_sha256": parent_hashes,
        "gene_order": list(gene_names),
        "starting_parent": starting_parent,
        "crossover_after": list(crossover_after),
        "inherited_from": inherited_from,
        "inherited": inherited,
    }
    provenance_digest = _digest(provenance_body)
    child_seed = {
        "schema": GENOTYPE_SCHEMA,
        "genome_id": child_id,
        "revision": "recombination-" + provenance_digest,
        "inherited": inherited,
    }
    try:
        child_seed = validate_seed(child_seed)
    except GenesisError as exc:
        raise RecombinationError("recombined child violates the GENESIS seed contract") from exc

    provenance = copy.deepcopy(provenance_body)
    provenance["digest"] = provenance_digest
    return {
        "schema": RECOMBINATION_SCHEMA,
        "seed": child_seed,
        "provenance": provenance,
    }


def verify_recombination(result: dict, parent_a: dict, parent_b: dict) -> bool:
    """Verify a public result against the two supplied parent seeds."""
    if (type(result) is not dict
            or any(type(key) is not str for key in result)
            or set(result) != {"schema", "seed", "provenance"}):
        return False
    if result.get("schema") != RECOMBINATION_SCHEMA:
        return False
    provenance = result.get("provenance")
    if (type(provenance) is not dict
            or any(type(key) is not str for key in provenance)
            or "digest" not in provenance):
        return False
    digest = provenance.get("digest")
    body = {key: value for key, value in provenance.items() if key != "digest"}
    try:
        if type(digest) is not str or _digest(body) != digest:
            return False
        child_seed = result["seed"]
        if (type(child_seed) is not dict
                or any(type(key) is not str for key in child_seed)
                or set(child_seed) != {"schema", "genome_id", "revision", "inherited"}):
            return False
        replay = recombine(
            parent_a, parent_b,
            child_genome_id=child_seed["genome_id"],
            starting_parent=provenance["starting_parent"],
            crossover_after=provenance["crossover_after"],
        )
        return replay == result
    except (KeyError, RecombinationError, TypeError, ValueError, RecursionError):
        return False
