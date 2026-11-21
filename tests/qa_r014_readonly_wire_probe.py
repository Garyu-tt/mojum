"""Offline read-only identification of persisted Mojum Aquarium wire contracts.

Not a migration engine, policy selector, authentication scheme or replay API.
This inspector reports *only* an allowlisted format and integrity metadata.
All events remain inside the opened local SQLite database. No external network,
imports from competing branch trees, writes or private event dumps.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


class WireProbeError(ValueError):
    """Journal integrity, unidentified ABI, or unsafe probe input."""


_EVENT = {
    "schema", "event_id", "agent_id", "topic", "observed_at_ns",
    "recorded_at_ns", "valid_from_ns", "valid_to_ns", "visibility",
    "source", "source_revision", "payload",
}
_COLUMNS = (
    "event_id", "agent_id", "topic", "visibility", "observed_at_ns",
    "recorded_at_ns", "valid_from_ns", "valid_to_ns",
)
_ZERO = "0" * 64
_V1 = "mojum.developmental.explorer/1"
_V2_A = "mojum.developmental.explorer.local-cell/2"
_V2_B = "mojum.developmental.local-cell/2"
_V3_B = "mojum.developmental.local-cell/3"


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WireProbeError("non-JSON event in stored journal") from exc


def _no_duplicate_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise WireProbeError("duplicate JSON key in journal event")
        result[key] = value
    return result


def _parse_wire(raw):
    if type(raw) is not str or len(raw.encode("utf-8")) > 128_000:
        raise WireProbeError("invalid or oversized stored event")
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               WireProbeError("nonfinite JSON token")))
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise WireProbeError("invalid stored JSON") from exc
    if type(value) is not dict or set(value) != _EVENT:
        raise WireProbeError("unknown event envelope")
    if raw != _canonical(value):
        raise WireProbeError("noncanonical stored JSON event")
    return value


def _format_for(manifest):
    if type(manifest) is not dict or manifest.get("schema") != "mojum.aquarium.bootstrap/1":
        raise WireProbeError("unknown Aquarium bootstrap")
    schema = manifest.get("developmental_schema")
    policy = manifest.get("developmental_policy")
    motor = manifest.get("motor_policy")
    motor_schema = manifest.get("motor_policy_schema")
    keys = {k for k in ("developmental_policy", "motor_policy", "motor_policy_schema")
            if k in manifest}
    if schema is None and not keys and "exploration_mode" not in manifest:
        return "legacy_scripted", None, None, None
    if schema == _V1 and not keys and manifest.get("exploration_mode") == "goal_free":
        return "legacy_coarse_v1", _V1, _V1, "bounded_goal_free_motor_play"
    if (schema == _V2_A and keys == {"motor_policy", "motor_policy_schema"}
            and motor == "local_cell_v2" and motor_schema == _V2_A
            and manifest.get("exploration_mode") == "goal_free"):
        return "local_cell_v2_a_unreleased", _V2_A, _V2_A, "bounded_local_cell_motor_play_v2"
    if (schema == _V2_B and keys == {"developmental_policy"}
            and policy == "local_cell_v2"
            and manifest.get("exploration_mode") == "goal_free"):
        return ("local_cell_v2_b_unreleased", _V2_B,
                "mojum.developmental.local-cell.outcome/2",
                "bounded_goal_free_local_cell_motor_play_v2")
    if (schema == _V3_B and keys == {"developmental_policy"}
            and policy == "local_cell_v3"
            and manifest.get("exploration_mode") == "goal_free"):
        return ("local_cell_v3_b_unreleased", _V3_B,
                "mojum.developmental.local-cell.outcome/3",
                "bounded_goal_free_local_cell_motor_play_v3")
    raise WireProbeError("ambiguous or unknown stored developmental policy ABI")


def inspect_journal(path, *, external_expected_head=None):
    """Verify a full local hash chain and report an explicit format label.

    A verified local chain is not cryptographically authenticated against a
    database owner who can rewrite the chain. Provide an independent head for
    stronger consistency checks. Never infers compatibility from the mode name.
    """
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.is_dir():
        raise WireProbeError("expected an existing nonsymlink SQLite file")
    if external_expected_head is not None and (
            type(external_expected_head) is not str
            or len(external_expected_head) != 64
            or any(c not in "0123456789abcdef" for c in external_expected_head)):
        raise WireProbeError("invalid independent expected head")
    prev, seq_expected, last_recorded = _ZERO, 1, -1
    kind = state_schema = learning_schema = action_source = None
    frames = 0
    try:
        with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            metadata = dict(db.execute("SELECT k,v FROM metadata"))
            if metadata.get("schema") != "mojum.noosphere.sqlite/1" or (
                    set(metadata) != {"schema", "owner"} or
                    not isinstance(metadata.get("owner"), str)):
                raise WireProbeError("not a recognized owned Noosphere database")
            owner = metadata["owner"]
            columns = ("seq,event_id,agent_id,topic,visibility,observed_at_ns,"
                       "recorded_at_ns,valid_from_ns,valid_to_ns,wire_json,"
                       "prev_hash,event_hash")
            for row in db.execute("SELECT " + columns + " FROM events ORDER BY seq"):
                wire = _parse_wire(row["wire_json"])
                seq = row["seq"]
                if type(seq) is not int or seq != seq_expected:
                    raise WireProbeError("noncontiguous SQLite event sequence")
                if (wire["schema"] != "mojum.noosphere.event/1" or
                        wire["agent_id"] != owner or
                        wire["visibility"] != "PRIVATE" or
                        wire["source"] != "mojum.aquarium.fixture" or
                        type(wire["payload"]) is not dict):
                    raise WireProbeError("unexpected event owner or source")
                if any(wire[name] != row[name] for name in _COLUMNS):
                    raise WireProbeError("indexed-column/event mismatch")
                if (any(type(wire[name]) is not int for name in
                        ("observed_at_ns", "recorded_at_ns", "valid_from_ns"))
                        or wire["recorded_at_ns"] < last_recorded
                        or wire["recorded_at_ns"] < wire["observed_at_ns"]):
                    raise WireProbeError("invalid journal timestamps")
                computed = hashlib.sha256(_canonical({
                    "previous": prev, "event": wire,
                }).encode("utf-8")).hexdigest()
                if row["prev_hash"] != prev or row["event_hash"] != computed:
                    raise WireProbeError("SQLite journal hash-chain mismatch")
                if seq == 1:
                    if (wire["topic"] != "aquarium.bootstrap"
                            or wire["event_id"] != "aquarium-bootstrap"):
                        raise WireProbeError("first row must bootstrap Aquarium")
                    kind, state_schema, learning_schema, action_source = (
                        _format_for(wire["payload"]))
                elif wire["topic"] == "aquarium.frame":
                    payload = wire["payload"]
                    if payload.get("schema") != "mojum.aquarium.frame/1":
                        raise WireProbeError("unknown Aquarium frame schema")
                    recorded = payload.get("developmental")
                    if state_schema is None:
                        if recorded is not None:
                            raise WireProbeError("unexpected developmental frame")
                    elif (type(recorded) is not dict
                          or recorded.get("schema") != state_schema
                          or type(recorded.get("learning")) is not dict
                          or recorded["learning"].get("schema") != learning_schema
                          or type(recorded.get("intent")) is not dict
                          or recorded["intent"].get("source") != action_source):
                        raise WireProbeError("frame disagrees with pinned policy ABI")
                    frames += 1
                elif wire["topic"] != "genesis.acquisition":
                    raise WireProbeError("unknown Aquarium event topic")
                prev, seq_expected, last_recorded = (
                    computed, seq_expected + 1, wire["recorded_at_ns"])
            db.execute("ROLLBACK")
    except sqlite3.Error as exc:
        raise WireProbeError("SQLite read-only probe failed") from exc
    if seq_expected == 1:
        raise WireProbeError("empty SQLite event journal")
    if external_expected_head is not None and prev != external_expected_head:
        raise WireProbeError("independent expected head differs")
    return {
        "schema": "mojum.qa.r014.readonly-wire-probe/1",
        "format": kind,
        "state_schema": state_schema,
        "events": seq_expected - 1,
        "frames": frames,
        "journal_head": prev,
        "integrity": ("chain-and-independent-head-checked" if external_expected_head
                      else "local-chain-checked-not-authenticated"),
        "source": "read-only SQLite; no event content exported",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-head")
    opts = parser.parse_args()
    try:
        print(json.dumps(inspect_journal(
            opts.db, external_expected_head=opts.expected_head),
            sort_keys=True, ensure_ascii=False))
    except WireProbeError as exc:
        print("ABORT: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
