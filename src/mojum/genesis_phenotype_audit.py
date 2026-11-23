"""Read-only bounded audit of a supplied GENESIS phenotype snapshot."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .genesis import GenesisError, GenomeLedger

SCHEMA = "mojum.genesis.phenotype-audit/1"
PUBLIC_CLASSIFICATION = "PUBLIC_SYNTHETIC"
MAX_INPUT_BYTES = 1_048_576
MAX_DEPTH = 24
MAX_NODES = 20_000


class PhenotypeAuditError(ValueError):
    """Malformed, unbounded, or non-public input was rejected."""


def _plain_json(
    value: Any,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    """Admit native JSON values while bounding work and raw UTF-8 size.

    The byte budget is charged during traversal, before whole-document JSON
    serialization can allocate a large temporary buffer. Diagnostic messages
    deliberately avoid echoing nested paths or caller-controlled long keys.
    """
    if budget is None:
        budget = [0, 0]
    budget[0] += 1
    if budget[0] > MAX_NODES:
        raise PhenotypeAuditError("input node budget exceeded")
    if depth > MAX_DEPTH:
        raise PhenotypeAuditError("input nesting limit exceeded")

    kind = type(value)
    if value is None or kind in (bool, int):
        return
    if kind is float:
        if not math.isfinite(value):
            raise PhenotypeAuditError("input contains a non-finite number")
        return
    if kind is str:
        if len(value) > 16_384:
            raise PhenotypeAuditError("input string limit exceeded")
        try:
            encoded_size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise PhenotypeAuditError("input contains text that is not valid UTF-8") from exc
        budget[1] += encoded_size
        if budget[1] > MAX_INPUT_BYTES:
            raise PhenotypeAuditError("input byte budget exceeded")
        return
    if kind is list:
        for item in value:
            _plain_json(item, depth + 1, budget)
        return
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise PhenotypeAuditError("input contains a non-string object key")
            _plain_json(key, depth + 1, budget)
            _plain_json(item, depth + 1, budget)
        return
    raise PhenotypeAuditError("input contains a non-native JSON value")


def _canonical_bytes(value: Any) -> bytes:
    _plain_json(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as exc:
        raise PhenotypeAuditError("input is not canonicalizable JSON") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise PhenotypeAuditError("input byte budget exceeded")
    return encoded


def _digest(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PhenotypeAudit:
    """Detached redacted result; hashes are consistency digests, not signatures."""

    schema: str
    accepted: bool
    at_tick: int
    ledger_revision: str
    event_count: int
    mismatch_fields: tuple[str, ...]
    computed_sha256: str
    supplied_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "accepted": self.accepted,
            "at_tick": self.at_tick,
            "ledger_revision": self.ledger_revision,
            "event_count": self.event_count,
            "mismatch_fields": list(self.mismatch_fields),
            "computed_sha256": self.computed_sha256,
            "supplied_sha256": self.supplied_sha256,
        }


def audit_phenotype_snapshot(
    ledger_snapshot: dict[str, Any],
    phenotype_snapshot: dict[str, Any],
    *,
    source_classification: str,
) -> PhenotypeAudit:
    """Recompute a phenotype from a PUBLIC_SYNTHETIC ledger and compare it."""
    if type(source_classification) is not str or source_classification != PUBLIC_CLASSIFICATION:
        raise PhenotypeAuditError("explicit PUBLIC_SYNTHETIC classification required")
    if type(ledger_snapshot) is not dict or type(phenotype_snapshot) is not dict:
        raise PhenotypeAuditError("ledger and phenotype snapshots must be plain dictionaries")

    _canonical_bytes(ledger_snapshot)
    supplied_bytes = _canonical_bytes(phenotype_snapshot)
    try:
        ledger = GenomeLedger.from_snapshot(copy.deepcopy(ledger_snapshot))
    except (GenesisError, TypeError, ValueError, OverflowError, KeyError) as exc:
        raise PhenotypeAuditError("ledger snapshot failed GenomeLedger validation") from exc

    tick = phenotype_snapshot.get("at_tick")
    if type(tick) is not int or not 0 <= tick <= 2**63 - 1:
        raise PhenotypeAuditError("phenotype at_tick must be a bounded native integer")

    computed = ledger.phenotype(tick)
    computed_bytes = _canonical_bytes(computed)
    fields = sorted(set(computed) | set(phenotype_snapshot))
    mismatches = tuple(
        key
        for key in fields
        if key not in computed
        or key not in phenotype_snapshot
        or computed[key] != phenotype_snapshot[key]
    )
    return PhenotypeAudit(
        SCHEMA,
        not mismatches,
        tick,
        ledger.revision,
        len(ledger_snapshot["events"]),
        mismatches,
        _digest(computed_bytes),
        _digest(supplied_bytes),
    )
