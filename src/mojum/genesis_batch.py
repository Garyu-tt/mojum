"""Detached all-or-nothing batch application for synthetic GENESIS acquisitions.

This module plans a batch against a validated snapshot. It never mutates a
caller-owned ledger or persists state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .genesis import GenesisError, GenomeLedger, canonical

BATCH_SCHEMA = "mojum.genesis.acquisition-batch/1"
MAX_BATCH_EVENTS = 64
MAX_BATCH_BYTES = 512 * 1024
MAX_EVENT_BYTES = 8 * 1024


@dataclass(frozen=True)
class BatchResult:
    """Immutable result; snapshot is returned as freshly decoded data on demand."""

    snapshot_json: str
    event_revisions: tuple[str, ...]
    batch_sha256: str
    base_revision: str
    final_revision: str

    @property
    def event_count(self) -> int:
        return len(self.event_revisions)

    def snapshot(self) -> dict[str, Any]:
        """Return a detached mutable copy, never the stored internal representation."""
        return json.loads(self.snapshot_json)


def _admit_json_tree(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    """Reject hostile Python objects before invoking JSON/equality operations."""
    if budget is None:
        budget = [0]
    if depth > 12:
        raise GenesisError("Batch input nesting exceeds limit")
    budget[0] += 1
    if budget[0] > 50_000:
        raise GenesisError("Batch input node budget exceeded")

    kind = type(value)
    if value is None or kind in (str, bool, int):
        if kind is str:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise GenesisError("Batch input contains invalid UTF-8") from exc
        return
    if kind is float:
        # GENESIS acquisition fields are integer/string-only. Reject floats
        # outright instead of permitting NaN/Infinity through generic JSON.
        raise GenesisError("Batch input floats are not supported")
    if kind is list:
        if len(value) > MAX_BATCH_EVENTS * 16:
            raise GenesisError("Batch input list exceeds limit")
        for item in value:
            _admit_json_tree(item, depth=depth + 1, budget=budget)
        return
    if kind is dict:
        if len(value) > 128:
            raise GenesisError("Batch input object exceeds key limit")
        for key, item in value.items():
            if type(key) is not str:
                raise GenesisError("Batch input keys must be native strings")
            _admit_json_tree(key, depth=depth + 1, budget=budget)
            _admit_json_tree(item, depth=depth + 1, budget=budget)
        return
    raise GenesisError("Batch input must contain only native JSON values")


def _bounded_json(value: Any, *, limit: int, label: str) -> str:
    _admit_json_tree(value)
    try:
        encoded = canonical(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GenesisError(f"{label} is not canonical JSON") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise GenesisError(f"{label} exceeds byte limit")
    return encoded


def apply_acquisition_batch(
    snapshot: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    expected_base_revision: str,
) -> BatchResult:
    """Apply 1..64 acquisitions to a detached ledger or reject the whole batch.

    The supplied snapshot and event list are read-only inputs. A result exists
    only after every event has passed the canonical GenomeLedger checks.
    """
    if type(expected_base_revision) is not str or len(expected_base_revision) != 64:
        raise GenesisError("expected_base_revision must be a SHA-256 revision")
    snapshot_json = _bounded_json(snapshot, limit=MAX_BATCH_BYTES, label="snapshot")
    events_json = _bounded_json(events, limit=MAX_BATCH_BYTES, label="events")
    if type(snapshot) is not dict or type(events) is not list:
        raise GenesisError("snapshot must be a native dict and events a native list")
    if not 1 <= len(events) <= MAX_BATCH_EVENTS:
        raise GenesisError(f"batch must contain 1..{MAX_BATCH_EVENTS} events")
    for index, event in enumerate(events):
        if type(event) is not dict:
            raise GenesisError(f"events[{index}] must be a native dict")
        event_json = _bounded_json(event, limit=MAX_EVENT_BYTES, label=f"events[{index}]")
        if len(event_json.encode("utf-8")) > MAX_EVENT_BYTES:
            raise GenesisError(f"events[{index}] exceeds byte limit")

    # Reparse admitted data to guarantee that the working state is fully
    # detached from nested caller-owned objects.
    detached_snapshot = json.loads(snapshot_json)
    detached_events = json.loads(events_json)
    ledger = GenomeLedger.from_snapshot(detached_snapshot)
    if ledger.revision != expected_base_revision:
        raise GenesisError("stale expected base revision")

    revisions: list[str] = []
    for event in detached_events:
        ledger.append(event, ledger.revision)
        revisions.append(ledger.revision)

    final_snapshot = ledger.snapshot()
    final_json = canonical(final_snapshot)
    receipt_body = {
        "schema": BATCH_SCHEMA,
        "base_revision": expected_base_revision,
        "final_revision": ledger.revision,
        "event_revisions": revisions,
        "event_count": len(revisions),
        "snapshot_sha256": hashlib.sha256(final_json.encode("utf-8")).hexdigest(),
    }
    digest = hashlib.sha256(canonical(receipt_body).encode("utf-8")).hexdigest()
    return BatchResult(
        snapshot_json=final_json,
        event_revisions=tuple(revisions),
        batch_sha256=digest,
        base_revision=expected_base_revision,
        final_revision=ledger.revision,
    )
