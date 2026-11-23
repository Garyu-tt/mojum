"""Bounded, read-only cloth stretch-threshold audit for synthetic fixtures.

This module reports geometric threshold exceedances. It does not simulate
cloth, estimate physical stress, or mutate a mesh.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "mojum.cloth.damage-audit/1"
PROVENANCE = "PUBLIC_SYNTHETIC"
MAX_SAMPLES = 4096
MAX_EDGE_ID = 96
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}\\Z")


class ClothDamageError(ValueError):
    """Raised when an audit request is malformed or exceeds its budget."""


@dataclass(frozen=True)
class DamageFinding:
    tick: int
    edge_id: str
    stretch_ratio: float
    exceeded: bool


@dataclass(frozen=True)
class DamageReport:
    schema: str
    provenance: str
    profile_revision: str
    threshold: float
    sample_count: int
    exceeded_count: int
    findings: tuple[DamageFinding, ...]
    digest: str


def _exact_number(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise ClothDamageError(f"{name} must be a native int or float")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ClothDamageError(f"{name} is outside finite numeric bounds") from None
    if not math.isfinite(result):
        raise ClothDamageError(f"{name} must be finite")
    return result


def audit_damage(request: Any) -> DamageReport:
    """Validate and classify a bounded PUBLIC_SYNTHETIC edge-strain batch.

    Request keys are exactly: schema, provenance, consent, profile_revision,
    threshold, samples. Each sample has exactly: tick, edge_id, stretch_ratio.
    Samples must be ordered by nondecreasing tick; (tick, edge_id) is unique.
    """
    if type(request) is not dict:
        raise ClothDamageError("request must be a native dict")
    # Check cardinality before constructing sets or hashing attacker-controlled
    # keys. A caller can otherwise force work proportional to an unbounded dict.
    request_keys = tuple(request)
    if len(request_keys) != 6 or any(type(key) is not str for key in request_keys):
        raise ClothDamageError("request fields do not match schema")
    if set(request_keys) != {
        "schema", "provenance", "consent", "profile_revision", "threshold", "samples"
    }:
        raise ClothDamageError("request fields do not match schema")
    if request["schema"] != SCHEMA:
        raise ClothDamageError("unsupported schema")
    if request["provenance"] != PROVENANCE or request["consent"] is not True:
        raise ClothDamageError("PUBLIC_SYNTHETIC provenance and explicit consent required")

    revision = request["profile_revision"]
    if type(revision) is not str or not _REVISION.fullmatch(revision):
        raise ClothDamageError("invalid profile_revision")

    threshold = _exact_number(request["threshold"], "threshold")
    if not 1.0 < threshold <= 100.0:
        raise ClothDamageError("threshold must be in (1, 100]")

    samples = request["samples"]
    if type(samples) is not list or len(samples) > MAX_SAMPLES:
        raise ClothDamageError("samples must be a bounded native list")

    findings: list[DamageFinding] = []
    seen: set[tuple[int, str]] = set()
    previous_tick = -1
    for index, sample in enumerate(samples):
        if type(sample) is not dict:
            raise ClothDamageError(f"sample {index} has invalid fields")
        sample_keys = tuple(sample)
        if len(sample_keys) != 3 or any(type(key) is not str for key in sample_keys):
            raise ClothDamageError(f"sample {index} has invalid fields")
        if set(sample_keys) != {"tick", "edge_id", "stretch_ratio"}:
            raise ClothDamageError(f"sample {index} has invalid fields")
        tick = sample["tick"]
        edge_id = sample["edge_id"]
        if type(tick) is not int or tick < 0 or tick > 2**63 - 1:
            raise ClothDamageError(f"sample {index} tick must be a bounded native int")
        if tick < previous_tick:
            raise ClothDamageError("samples must be ordered by nondecreasing tick")
        previous_tick = tick
        if type(edge_id) is not str or not edge_id or len(edge_id) > MAX_EDGE_ID:
            raise ClothDamageError(f"sample {index} edge_id is invalid")
        identity = (tick, edge_id)
        if identity in seen:
            raise ClothDamageError("duplicate edge sample at the same tick")
        seen.add(identity)
        ratio = _exact_number(sample["stretch_ratio"], f"sample {index} stretch_ratio")
        if not 0.0 < ratio <= 1000.0:
            raise ClothDamageError(f"sample {index} stretch_ratio is outside bounds")
        findings.append(DamageFinding(tick, edge_id, ratio, ratio > threshold))

    canonical = {
        "schema": SCHEMA,
        "provenance": PROVENANCE,
        "profile_revision": revision,
        "threshold": threshold,
        "findings": [
            {"tick": f.tick, "edge_id": f.edge_id, "stretch_ratio": f.stretch_ratio,
             "exceeded": f.exceeded}
            for f in findings
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    frozen_findings = tuple(findings)
    return DamageReport(
        schema=SCHEMA,
        provenance=PROVENANCE,
        profile_revision=revision,
        threshold=threshold,
        sample_count=len(frozen_findings),
        exceeded_count=sum(f.exceeded for f in frozen_findings),
        findings=frozen_findings,
        digest=digest,
    )
