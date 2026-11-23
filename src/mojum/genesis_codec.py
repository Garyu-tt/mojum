"""Bounded, strict JSON interchange for synthetic GENESIS ledger fixtures.

This module is deliberately an offline codec, not a persistence backend:
it does not authenticate, encrypt, replicate, or write to Noosphere.
"""
from __future__ import annotations

import json

from .genesis import GenesisError, GenomeLedger, canonical

MAX_SNAPSHOT_BYTES = 1_048_576
"""Maximum encoded ledger size (1 MiB), checked before JSON parsing."""


class GenesisCodecError(ValueError):
    """A ledger fixture could not be encoded or safely restored."""


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def dumps(ledger: GenomeLedger) -> bytes:
    """Return canonical UTF-8 bytes for one validated in-memory ledger.

    The output is deterministic for a given ledger revision. It is not
    encrypted or signed and must not be treated as a private-data export.
    """
    if type(ledger) is not GenomeLedger:
        raise GenesisCodecError("ledger must be an exact GenomeLedger instance")
    try:
        encoded = canonical(ledger.snapshot()).encode("utf-8")
    except (GenesisError, TypeError, ValueError, UnicodeError) as exc:
        raise GenesisCodecError("Ledger snapshot is not canonical JSON") from exc
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise GenesisCodecError("Ledger snapshot exceeds the byte limit")
    return encoded


def loads(payload: bytes | str) -> GenomeLedger:
    """Restore a ledger from bounded UTF-8 JSON and verify its full revision.

    Duplicate keys are rejected at every object depth. The ledger's existing
    schema and event validation remain the authority for semantic acceptance.
    """
    if type(payload) is bytes:
        raw = payload
    elif type(payload) is str:
        # UTF-8 uses at least one byte per Unicode code point. Reject an
        # obviously oversized text payload before allocating its encoded copy.
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise GenesisCodecError("Snapshot exceeds the byte limit")
        try:
            raw = payload.encode("utf-8")
        except UnicodeError as exc:
            raise GenesisCodecError("Snapshot text is not valid UTF-8") from exc
    else:
        raise GenesisCodecError("payload must be bytes or str")

    if not raw:
        raise GenesisCodecError("Snapshot must not be empty")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise GenesisCodecError("Snapshot exceeds the byte limit")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise GenesisCodecError("UTF-8 BOM is not permitted")

    try:
        text = raw.decode("utf-8", errors="strict")
        snapshot = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateKeyError as exc:
        raise GenesisCodecError(str(exc)) from exc
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise GenesisCodecError("Snapshot is not strict UTF-8 JSON") from exc

    if type(snapshot) is not dict:
        raise GenesisCodecError("Snapshot root must be a JSON object")
    try:
        return GenomeLedger.from_snapshot(snapshot)
    except (GenesisError, TypeError, ValueError, KeyError, RecursionError) as exc:
        raise GenesisCodecError("Snapshot failed GENESIS ledger validation") from exc
