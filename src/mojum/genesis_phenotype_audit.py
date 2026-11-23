"""Bounded read-only consistency audit for a GENESIS phenotype projection."""
from __future__ import annotations

from mojum.genesis import GenesisError, GenomeLedger, canonical, sha256

SCHEMA = "mojum.genesis.phenotype-audit/1"
MAX_INPUT_BYTES = 1_048_576
MAX_TREE_NODES = 20_000
MAX_TREE_DEPTH = 32
MAX_TICK = 2**63 - 1


class GenesisPhenotypeAuditError(ValueError):
    """Malformed or unsupported audit input."""


def _check_tree(value, path="$", depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_TREE_NODES:
        raise GenesisPhenotypeAuditError("input node budget exceeded")
    if depth > MAX_TREE_DEPTH:
        raise GenesisPhenotypeAuditError("input nesting budget exceeded")
    kind = type(value)
    if kind is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise GenesisPhenotypeAuditError(f"{path} has a non-string key")
            _check_tree(child, f"{path}.{key}", depth + 1, budget)
    elif kind is list:
        for index, child in enumerate(value):
            _check_tree(child, f"{path}[{index}]", depth + 1, budget)
    elif value is None or kind in (str, int, bool, float):
        if kind is str:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise GenesisPhenotypeAuditError(
                    f"{path} contains invalid Unicode"
                ) from exc
    else:
        raise GenesisPhenotypeAuditError(f"{path} is not a native JSON value")


def _bounded_canonical(value, path):
    _check_tree(value, path)
    try:
        encoded = canonical(value)
        if len(encoded.encode("utf-8")) > MAX_INPUT_BYTES:
            raise GenesisPhenotypeAuditError(f"{path} exceeds the 1 MiB input limit")
        return encoded
    except GenesisPhenotypeAuditError:
        raise
    except (GenesisError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise GenesisPhenotypeAuditError(f"{path} is not canonical JSON") from exc


def audit_phenotype(ledger_snapshot, phenotype_snapshot, tick):
    """Compare an observed phenotype to the canonical ledger projection.

    Returns a deterministic metadata-only report. Malformed inputs raise
    GenesisPhenotypeAuditError; a well-formed but incorrect projection returns
    status='mismatch'. No storage, network, or runtime state is accessed.
    """
    if type(tick) is not int or not 0 <= tick <= MAX_TICK:
        raise GenesisPhenotypeAuditError("tick must be an integer in range")
    ledger_wire = _bounded_canonical(ledger_snapshot, "ledger_snapshot")
    phenotype_wire = _bounded_canonical(phenotype_snapshot, "phenotype_snapshot")
    if type(ledger_snapshot) is not dict or type(phenotype_snapshot) is not dict:
        raise GenesisPhenotypeAuditError("snapshots must be native JSON objects")
    try:
        ledger = GenomeLedger.from_snapshot(ledger_snapshot)
        expected = ledger.phenotype(tick)
    except (GenesisError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        raise GenesisPhenotypeAuditError(
            "ledger snapshot failed GENESIS validation"
        ) from exc

    expected_wire = canonical(expected)
    expected_hash = sha256(expected)
    observed_hash = sha256(phenotype_snapshot)
    expected_keys = set(expected)
    observed_keys = set(phenotype_snapshot)
    mismatches = sorted(
        key for key in expected_keys | observed_keys
        if key not in expected_keys or key not in observed_keys
        or canonical(expected.get(key)) != canonical(phenotype_snapshot.get(key))
    )
    matched = expected_wire == phenotype_wire
    return {
        "schema": SCHEMA,
        "status": "match" if matched else "mismatch",
        "at_tick": tick,
        "ledger_revision": ledger.revision,
        "expected_phenotype_sha256": expected_hash,
        "observed_phenotype_sha256": observed_hash,
        "mismatched_fields": [] if matched else mismatches,
        "source_classification": "PUBLIC_SYNTHETIC_audit_metadata_only",
    }
