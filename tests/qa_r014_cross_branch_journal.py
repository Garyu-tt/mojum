"""Independent, offline R0.14 SQLite wire-ABI acceptance test.

This test runs two immutable GitHub checkouts in separate subprocesses.
Only disposable synthetic event journals are created. No imports or files
from either checkout are mixed or changed, and no journal is migrated.

Default mode characterizes a current blocker and exits 0 on confirmed
fail-closed behavior. --require-compatible is a separate RELEASE GATE:
it exits nonzero until both v2 foreign reopens and exact wire identities
are compatible. Never interpret a green characterization as release approval.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_pinned_checkout(source: Path, expected_sha: str, marker: str):
    """Never attribute results to a historical PR without its exact git HEAD."""
    check((source / marker).is_file(), "missing pinned policy module: " + marker)
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AssertionError("cannot verify checkout provenance") from exc
    check(completed.returncode == 0, "source is not a verifiable git checkout")
    check(completed.stdout.strip() == expected_sha,
          "checkout HEAD differs from pinned source " + expected_sha)


def invoke(source: Path, database: Path, *, kind: str, ticks: int):
    env = os.environ.copy()
    env["PYTHONPATH"] = str((source / "src").resolve())
    command = [
        sys.executable, "-m", "mojum.aquarium",
        "--db", str(database), "--ticks", str(ticks),
        "--exploration-mode", "goal_free",
        "--genome", str(source / "examples" / "genesis_seed.json"),
        "--graph", str(source / "examples" / "first_loop.json"),
    ]
    if kind == "v2-a":
        command += ["--motor-policy", "local_cell_v2"]
    elif kind == "v2-b":
        command += ["--developmental-policy", "local_cell_v2"]
    elif kind != "legacy":
        raise AssertionError("unknown test policy kind")
    result = subprocess.run(
        command, cwd=source, env=env, capture_output=True,
        text=True, timeout=35, check=False,
    )
    if result.returncode:
        return None, result.stderr[-2000:]
    try:
        return json.loads(result.stdout), ""
    except json.JSONDecodeError as exc:
        raise AssertionError("Aquarium returned a non-JSON checkpoint") from exc


def database_state(database: Path):
    # Do not invoke an Aquarium opener to inspect failed foreign migrations.
    # Reading through SQLite's read-only URI preserves the data by design.
    with closing(sqlite3.connect(
        "file:" + str(database) + "?mode=ro", uri=True,
    )) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT seq,event_id,prev_hash,event_hash,wire_json "
            "FROM events ORDER BY seq"
        ).fetchall()
    check(bool(rows), "missing synthetic bootstrap")
    check(rows[0][1] == "aquarium-bootstrap", "not an Aquarium history")
    hash_stream = hashlib.sha256()
    for row in rows:
        hash_stream.update(json.dumps(
            row, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8"))
        hash_stream.update(b"\n")
    bootstrap = json.loads(rows[0][4])["payload"]
    # One closed-world synthetic trial records motor fields on the first frame.
    frame = json.loads(rows[1][4])["payload"] if len(rows) > 1 else {}
    return {
        "count": len(rows), "head": rows[-1][3],
        "wire_digest": hash_stream.hexdigest(),
        "manifest": bootstrap,
        "first_frame": frame,
    }


def public_contract(state, checkpoint):
    manifest = state["manifest"]
    frame = state["first_frame"].get("developmental", {})
    learning = frame.get("learning", {})
    intent = frame.get("intent", {})
    snapshot = checkpoint.get("developmental_state")
    check(isinstance(snapshot, dict), "no versioned developmental snapshot")
    return {
        "policy_keys": sorted(
            key for key in ("motor_policy", "motor_policy_schema",
                            "developmental_policy")
            if key in manifest
        ),
        "developmental_schema": manifest.get("developmental_schema"),
        "snapshot_schema": snapshot.get("schema"),
        "snapshot_fields": sorted(snapshot),
        "event_developmental_schema": frame.get("schema"),
        "learning_schema": learning.get("schema"),
        "intent_source": intent.get("source"),
    }


def same_state(before, after, context):
    check(
        (before["count"], before["head"], before["wire_digest"]) ==
        (after["count"], after["head"], after["wire_digest"]),
        context + " mutated source journal rows on a zero-tick operation",
    )


def self_reopen(source, db, kind):
    first, err = invoke(source, db, kind=kind, ticks=4)
    check(first is not None, "own v2 journal creation failed: " + err[:400])
    check(first["tick"] == 4 and first["stored_events"] == 5,
          "unexpected own v2 event count")
    before = database_state(db)
    reopened, err = invoke(source, db, kind=kind, ticks=0)
    check(reopened is not None, "own v2 cold replay failed: " + err[:400])
    check(reopened["journal_head"] == first["journal_head"],
          "own v2 head changed on cold replay")
    same_state(before, database_state(db), "own cold replay")
    return first, before


def foreign_reopen(source, db, kind, origin):
    before = database_state(db)
    opened, error = invoke(source, db, kind=kind, ticks=0)
    after = database_state(db)
    same_state(before, after, origin + " -> foreign")
    if opened is None:
        # Not a parser error. The foreign host accepted its OWN CLI flag but
        # correctly rejected a differently defined stored bootstrap contract.
        check("mismatch" in error.lower(),
              origin + " foreign failure was not a manifest mismatch: "
              + error[:450])
        return False
    check(opened["stored_events"] == before["count"], "foreign event count drift")
    check(opened["journal_head"] == before["head"], "foreign head drift")
    return True


def legacy_cross_test(a, b, scratch):
    for label, creator, reader in (("a-to-b", a, b), ("b-to-a", b, a)):
        db = scratch / (label + ".legacy.sqlite3")
        first, error = invoke(creator, db, kind="legacy", ticks=4)
        check(first is not None, "legacy creator failed: " + error[:450])
        before = database_state(db)
        crossed, error = invoke(reader, db, kind="legacy", ticks=0)
        check(crossed is not None, "old legacy cross replay failed: " + error[:450])
        check(crossed["journal_head"] == first["journal_head"],
              "old legacy journal head changed in foreign reader")
        same_state(before, database_state(db), "legacy cross replay")
        appended, error = invoke(reader, db, kind="legacy", ticks=1)
        check(appended is not None, "legacy foreign append failed: " + error[:450])
        check(appended["tick"] == 5 and appended["stored_events"] == 6,
              "legacy cross replay did not append exactly one frame")
        final, error = invoke(creator, db, kind="legacy", ticks=0)
        check(final is not None, "legacy original failed to reopen: " + error[:450])
        check(final["journal_head"] == appended["journal_head"],
              "legacy original did not adopt foreign append")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-a", type=Path, required=True)
    parser.add_argument("--source-b", type=Path, required=True)
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args()
    a, b = args.source_a.resolve(), args.source_b.resolve()
    assert_pinned_checkout(
        a, "8d2eaf234c09177ae0b92dfef4bb41950529c912",
        "src/mojum/developmental_policy_v2.py",
    )
    assert_pinned_checkout(
        b, "b755da530634c0d6b8866107e7b4e9e325839175",
        "src/mojum/cell_policy_v2.py",
    )
    with tempfile.TemporaryDirectory(prefix="mojum-r014-wire-private-") as tmp:
        scratch = Path(tmp)
        legacy_cross_test(a, b, scratch)
        a_db, b_db = scratch / "a.private.sqlite3", scratch / "b.private.sqlite3"
        a_self, a_state = self_reopen(a, a_db, "v2-a")
        b_self, b_state = self_reopen(b, b_db, "v2-b")
        a_contract = public_contract(a_state, a_self)
        b_contract = public_contract(b_state, b_self)
        a_to_b = foreign_reopen(b, a_db, "v2-b", "a-to-b")
        b_to_a = foreign_reopen(a, b_db, "v2-a", "b-to-a")
        same_state(a_state, database_state(a_db), "a v2 foreign replay")
        same_state(b_state, database_state(b_db), "b v2 foreign replay")
        wire_identity = a_contract == b_contract
        compatible = a_to_b and b_to_a and wire_identity
        report = {
            "schema": "mojum.qa.r014.cross-wire/1",
            "legacy_two_way_replay_and_append": "PASS",
            "v2_self_reopen": {"112": "PASS", "115": "PASS"},
            "v2_foreign_reopen": {
                "112_journal_under_115": "PASS" if a_to_b else "REJECTED",
                "115_journal_under_112": "PASS" if b_to_a else "REJECTED",
            },
            "v2_journal_unchanged_after_foreign_open": "PASS",
            "wire_contract_identical": wire_identity,
            "contract_112": a_contract,
            "contract_115": b_contract,
            "status": ("ABI_COMPATIBLE" if compatible
                       else "BLOCKED_INCOMPATIBLE_V2_WIRE"),
            "private_event_data": "not exported; disposable synthetic journals",
        }
        print(json.dumps(report, sort_keys=True, ensure_ascii=False))
        if args.require_compatible and not compatible:
            print("RELEASE GATE BLOCKED: identical public name local_cell_v2 "
                  "does not have verified two-way SQLite wire compatibility",
                  file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, subprocess.TimeoutExpired) as exc:
        print("ACCEPTANCE TEST FAILURE: " + str(exc), file=sys.stderr)
        sys.exit(2)
