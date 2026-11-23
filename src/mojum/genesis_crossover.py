"""Deterministic two-parent crossover for synthetic GENESIS seed fixtures.

This is a pure, opt-in data transform. It does not model biology, copy acquired
history, write a ledger, or grant authority to a child genome.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .genesis import (
    GENES,
    GENOTYPE_SCHEMA,
    GenesisError,
    GenomeLedger,
    validate_seed,
)

CROSSOVER_SCHEMA = "mojum.genesis.crossover/1"
RESULT_SCHEMA = "mojum.genesis.crossover-result/1"
MAX_SEED = 2**64 - 1
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\\Z")
_DOMAIN = b"mojum.genesis.crossover/1\\x00"


def _identifier(value: object, path: str) -> str:
    if type(value) is not str or not _ID_RE.fullmatch(value):
        raise GenesisError(f"{path} must be a bounded ASCII identifier")
    return value


def _plain_seed(value: object, path: str) -> dict:
    """Validate exact native JSON-shaped seed input before equality checks."""
    if type(value) is not dict:
        raise GenesisError(f"{path} must be an exact dict")
    required = {"schema", "genome_id", "revision", "inherited"}
    if any(type(key) is not str for key in value):
        raise GenesisError(f"{path} keys must be native strings")
    if set(value) != required:
        raise GenesisError(f"{path} must contain exactly {sorted(required)}")
    if type(value["schema"]) is not str or value["schema"] != GENOTYPE_SCHEMA:
        raise GenesisError(f"{path} has unsupported schema")
    _identifier(value["genome_id"], f"{path}.genome_id")
    if type(value["revision"]) is not str or not value["revision"] or len(value["revision"]) > 128:
        raise GenesisError(f"{path}.revision must be a bounded nonempty string")
    try:
        value["revision"].encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GenesisError(f"{path}.revision must be valid UTF-8") from exc
    inherited = value["inherited"]
    if type(inherited) is not dict:
        raise GenesisError(f"{path}.inherited must be an exact dict")
    if any(type(key) is not str for key in inherited):
        raise GenesisError(f"{path}.inherited keys must be native strings")
    if set(inherited) != set(GENES):
        raise GenesisError(f"{path}.inherited must contain exactly the supported genes")
    try:
        checked = validate_seed(value)
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, GenesisError):
            raise
        raise GenesisError(f"{path} is invalid") from exc
    return checked


def _canonical(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise GenesisError("crossover result is not canonical UTF-8 JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CrossoverResult:
    """Immutable result envelope; mapping properties return fresh detached copies."""

    _payload_json: str
    digest: str

    @property
    def schema(self) -> str:
        return RESULT_SCHEMA

    @property
    def seed(self) -> dict:
        return json.loads(self._payload_json)["seed"]

    @property
    def parents(self) -> list[dict]:
        return json.loads(self._payload_json)["parents"]

    @property
    def loci(self) -> list[dict]:
        return json.loads(self._payload_json)["loci"]

    @property
    def random_seed(self) -> int:
        return json.loads(self._payload_json)["random_seed"]

    def to_dict(self) -> dict:
        """Return a detached JSON-compatible report with a consistency digest."""
        result = json.loads(self._payload_json)
        result["digest"] = self.digest
        return result


def cross_genomes(
    parent_a: object,
    parent_b: object,
    *,
    child_genome_id: str,
    random_seed: int,
) -> CrossoverResult:
    """Create one child seed by selecting exactly one parent's value per gene.

    Selection uses the low bit of a domain-separated SHA-256 digest for each
    locus, so replay does not depend on Python's random module implementation.
    Parent argument order is meaningful and is retained in provenance.
    """
    a = _plain_seed(parent_a, "parent_a")
    b = _plain_seed(parent_b, "parent_b")
    child_id = _identifier(child_genome_id, "child_genome_id")
    if type(random_seed) is not int or not 0 <= random_seed <= MAX_SEED:
        raise GenesisError(f"random_seed must be an integer in [0, {MAX_SEED}]")
    if a["genome_id"] == b["genome_id"]:
        raise GenesisError("parents must have distinct genome_id values")
    if child_id in (a["genome_id"], b["genome_id"]):
        raise GenesisError("child_genome_id must differ from both parents")

    parent_a_digest = _digest(a)
    parent_b_digest = _digest(b)
    seed_bytes = random_seed.to_bytes(8, "big")
    inherited: dict[str, int] = {}
    loci: list[dict[str, object]] = []

    for gene in sorted(GENES):
        selector_material = (
            _DOMAIN
            + seed_bytes
            + bytes.fromhex(parent_a_digest)
            + bytes.fromhex(parent_b_digest)
            + b"\\x00"
            + gene.encode("ascii")
        )
        choose_b = hashlib.sha256(selector_material).digest()[0] & 1
        selected = b if choose_b else a
        source = "parent_b" if choose_b else "parent_a"
        inherited[gene] = selected["inherited"][gene]
        loci.append({
            "gene": gene,
            "source": source,
            "parent_genome_id": selected["genome_id"],
            "value": inherited[gene],
        })

    parent_receipts = [
        {
            "slot": "parent_a",
            "genome_id": a["genome_id"],
            "revision": a["revision"],
            "seed_sha256": parent_a_digest,
        },
        {
            "slot": "parent_b",
            "genome_id": b["genome_id"],
            "revision": b["revision"],
            "seed_sha256": parent_b_digest,
        },
    ]

    revision_material = {
        "schema": CROSSOVER_SCHEMA,
        "child_genome_id": child_id,
        "random_seed": random_seed,
        "parents": parent_receipts,
        "loci": loci,
        "inherited": inherited,
    }
    child_revision = "crossover-1-" + _digest(revision_material)
    child_seed = {
        "schema": GENOTYPE_SCHEMA,
        "genome_id": child_id,
        "revision": child_revision,
        "inherited": inherited,
    }
    try:
        GenomeLedger(child_seed)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GenesisError("generated child seed is not accepted by GenomeLedger") from exc

    payload = {
        "schema": RESULT_SCHEMA,
        "random_seed": random_seed,
        "parents": parent_receipts,
        "loci": loci,
        "seed": child_seed,
    }
    payload_json = _canonical(payload)
    return CrossoverResult(_payload_json=payload_json, digest=_digest(payload))
