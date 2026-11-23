"""Deterministic synthetic founder crossing for GENESIS v1 seed fixtures.

This is an opt-in fixture operator, not a biological reproduction model.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping

from .genesis import (
    GENES,
    GENOTYPE_SCHEMA,
    GenesisError,
    sha256,
    validate_seed,
)

CROSS_SCHEMA = "mojum.genesis.cross/1"
MAX_CROSS_SEED = 2**63 - 1


def _validate_cross_seed(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_CROSS_SEED:
        raise GenesisError(f"cross_seed must be an integer in [0, {MAX_CROSS_SEED}]")
    return value


def _validate_child_id(value: object, parent_ids: set[str]) -> str:
    if type(value) is not str or not value.strip() or len(value) > 128:
        raise GenesisError("child_genome_id must be a nonempty string (<= 128)")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GenesisError("child_genome_id must be valid UTF-8") from exc
    if value in parent_ids:
        raise GenesisError("child_genome_id must differ from both parent identities")
    return value


def _choose_parent(
    cross_seed: int, gene: str, parent_a_sha: str, parent_b_sha: str
) -> int:
    """Choose A or B with a stable SHA-256 bit, independent of Python RNG."""
    material = (
        f"{CROSS_SCHEMA}\0{cross_seed}\0{gene}\0{parent_a_sha}\0{parent_b_sha}"
    ).encode("utf-8")
    return hashlib.sha256(material).digest()[0] & 1


def cross_seeds(
    parent_a: Mapping,
    parent_b: Mapping,
    *,
    cross_seed: int,
    child_genome_id: str,
) -> dict:
    """Create a detached synthetic child seed and an integrity-only receipt.

    For each existing GENESIS v1 gene, exactly one parent's inherited value is
    selected. Parent order is significant. The caller supplies the complete
    identity and integer seed; no hidden randomness or global state is used.
    """
    checked_a = validate_seed(parent_a)
    checked_b = validate_seed(parent_b)
    seed = _validate_cross_seed(cross_seed)

    parent_ids = {checked_a["genome_id"], checked_b["genome_id"]}
    if len(parent_ids) != 2:
        raise GenesisError("parents must have distinct genome identities")
    child_id = _validate_child_id(child_genome_id, parent_ids)

    a_sha = sha256(checked_a)
    b_sha = sha256(checked_b)
    inherited: dict[str, int] = {}
    selections: dict[str, dict[str, object]] = {}

    for gene in sorted(GENES):
        selected = _choose_parent(seed, gene, a_sha, b_sha)
        source_name = "parent_a" if selected == 0 else "parent_b"
        source = checked_a if selected == 0 else checked_b
        value = source["inherited"][gene]
        inherited[gene] = value
        selections[gene] = {"source": source_name, "value": value}

    child_material = {
        "schema": CROSS_SCHEMA,
        "cross_seed": seed,
        "parent_a_sha256": a_sha,
        "parent_b_sha256": b_sha,
        "child_genome_id": child_id,
        "inherited": inherited,
    }
    child_revision = f"cross:{sha256(child_material)}"
    child_seed = {
        "schema": GENOTYPE_SCHEMA,
        "genome_id": child_id,
        "revision": child_revision,
        "inherited": inherited,
    }
    # Reuse the canonical v1 validator as the sole child wire-contract authority.
    child_seed = validate_seed(child_seed)

    receipt_body = {
        "schema": CROSS_SCHEMA,
        "cross_seed": seed,
        "parent_a": {
            "genome_id": checked_a["genome_id"],
            "seed_sha256": a_sha,
        },
        "parent_b": {
            "genome_id": checked_b["genome_id"],
            "seed_sha256": b_sha,
        },
        "child_seed_sha256": sha256(child_seed),
        "selections": selections,
        "method": "sha256-bit-per-gene-parent-selection/1",
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256(receipt_body),
        "integrity_notice": "consistency_digest_not_authentication",
    }

    # All nested objects are newly allocated; callers cannot mutate either input.
    return {"child_seed": copy.deepcopy(child_seed), "receipt": copy.deepcopy(receipt)}


def verify_cross_receipt(result: object) -> bool:
    """Check receipt self-consistency and child digest without authenticating it."""
    if type(result) is not dict or set(result) != {"child_seed", "receipt"}:
        raise GenesisError("cross result must contain child_seed and receipt")
    child = validate_seed(result["child_seed"])
    receipt = result["receipt"]
    required = {
        "schema", "cross_seed", "parent_a", "parent_b", "child_seed_sha256",
        "selections", "method", "receipt_sha256", "integrity_notice",
    }
    if type(receipt) is not dict or set(receipt) != required:
        raise GenesisError("invalid cross receipt fields")
    if receipt["schema"] != CROSS_SCHEMA:
        raise GenesisError("unsupported cross receipt schema")
    _validate_cross_seed(receipt["cross_seed"])
    if receipt["method"] != "sha256-bit-per-gene-parent-selection/1":
        raise GenesisError("unsupported cross selection method")
    if receipt["integrity_notice"] != "consistency_digest_not_authentication":
        raise GenesisError("invalid receipt integrity notice")
    for key in ("parent_a", "parent_b"):
        parent = receipt[key]
        if type(parent) is not dict or set(parent) != {"genome_id", "seed_sha256"}:
            raise GenesisError(f"invalid {key} receipt")
        if type(parent["genome_id"]) is not str or not parent["genome_id"].strip():
            raise GenesisError(f"invalid {key} genome identity")
        digest = parent["seed_sha256"]
        if type(digest) is not str or len(digest) != 64:
            raise GenesisError(f"invalid {key} digest")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise GenesisError(f"invalid {key} digest") from exc
    if receipt["parent_a"]["genome_id"] == receipt["parent_b"]["genome_id"]:
        raise GenesisError("parent identities must be distinct")
    if child["genome_id"] in {
        receipt["parent_a"]["genome_id"], receipt["parent_b"]["genome_id"]
    }:
        raise GenesisError("child identity collides with a parent")
    if sha256(child) != receipt["child_seed_sha256"]:
        raise GenesisError("child seed digest mismatch")
    body = {key: receipt[key] for key in required if key != "receipt_sha256"}
    if sha256(body) != receipt["receipt_sha256"]:
        raise GenesisError("cross receipt digest mismatch")
    if type(receipt["selections"]) is not dict or set(receipt["selections"]) != set(GENES):
        raise GenesisError("invalid gene selection inventory")
    for gene, selection in receipt["selections"].items():
        if type(selection) is not dict or set(selection) != {"source", "value"}:
            raise GenesisError(f"invalid selection for {gene}")
        source = selection["source"]
        if type(source) is not str or source not in ("parent_a", "parent_b"):
            raise GenesisError(f"invalid selection source for {gene}")
        if type(selection["value"]) is not int:
            raise GenesisError(f"invalid selected value for {gene}")
        if child["inherited"][gene] != selection["value"]:
            raise GenesisError(f"child value differs from receipt for {gene}")
    return True
