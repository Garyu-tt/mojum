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


def _json_string_size(value):
    """Return the exact UTF-8 size of json.dumps' quoted string form."""
    size = 2  # surrounding quotes
    short_escapes = {8, 9, 10, 12, 13}
    for char in value:
        code = ord(char)
        if code in (34, 92):
            size += 2
        elif code < 32:
            size += 2 if code in short_escapes else 6
        elif 0xD800 <= code <= 0xDFFF:
            raise GenesisPhenotypeAuditError("input contains invalid Unicode")
        elif code <= 0x7F:
            size += 1
        elif code <= 0x7FF:
            size += 2
        elif code <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > MAX_INPUT_BYTES:
            raise GenesisPhenotypeAuditError("input exceeds the 1 MiB JSON budget")
    return size


def _check_tree(value, path="$"):
    """Preflight exact JSON serialization size before allocating serialized input."""
    stack = [(value, 0)]
    nodes = 0
    encoded_size = 0

    def add_size(amount):
        nonlocal encoded_size
        encoded_size += amount
        if encoded_size > MAX_INPUT_BYTES:
            raise GenesisPhenotypeAuditError(
                f"{path} exceeds the 1 MiB JSON input limit"
            )

    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_TREE_NODES:
            raise GenesisPhenotypeAuditError("input node budget exceeded")
        if depth > MAX_TREE_DEPTH:
            raise GenesisPhenotypeAuditError("input nesting budget exceeded")

        kind = type(item)
        if kind is dict:
            add_size(2 + max(0, len(item) - 1))  # braces and commas
            for key, child in item.items():
                if type(key) is not str:
                    raise GenesisPhenotypeAuditError(
                        f"{path} contains a non-string object key"
                    )
                add_size(_json_string_size(key) + 1)  # key and colon
                stack.append((child, depth + 1))
        elif kind is list:
            add_size(2 + max(0, len(item) - 1))  # brackets and commas
            for child in item:
                stack.append((child, depth + 1))
        elif kind is str:
            add_size(_json_string_size(item))
        elif kind is int:
            # GENESIS counters and parameters are signed 63-bit values.
            if not -(2**63) <= item <= MAX_TICK:
                raise GenesisPhenotypeAuditError("input integer is out of range")
            add_size(len(str(item)))
        elif kind is float:
            if not math.isfinite(item):
                raise GenesisPhenotypeAuditError("input contains a non-finite number")
            add_size(len(repr(item)))
        elif kind is bool:
            add_size(5 if item else 5)  # true/false are both five bytes
        elif item is None:
            add_size(4)
        else:
            raise GenesisPhenotypeAuditError(
                f"{path} contains a non-native JSON value"
            )


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
