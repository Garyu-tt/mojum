#!/usr/bin/env python3
"""Disposable, offline-oriented Mojum Aquarium cold-restart smoke audit.

This is a synthetic fixture diagnostic, not a privacy audit or process sandbox.
It neither opens a caller-supplied journal nor exposes event payloads in reports.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

SCHEMA = "mojum.qa.offline-restart-smoke/1"
ROOT = Path(__file__).resolve().parent.parent
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MAX_OUTPUT = 65536
MAX_BATCH = 32


class SmokeFailure(ValueError):
    """An admission or synthetic replay invariant failed."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SmokeFailure("duplicate JSON member in CLI checkpoint")
        result[key] = value
    return result


def _checkpoint(raw, *, tick, events):
    if type(raw) is not str or len(raw.encode("utf-8")) > MAX_OUTPUT:
        raise SmokeFailure("CLI checkpoint exceeds output budget")
    try:
        result = json.loads(raw, object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                SmokeFailure("nonfinite checkpoint number")))
    except (ValueError, TypeError) as exc:
        raise SmokeFailure("CLI emitted invalid checkpoint JSON") from exc
    if type(result) is not dict:
        raise SmokeFailure("CLI checkpoint must be an object")
    checks = {
        "schema": "mojum.aquarium.checkpoint/1", "tick": tick,
        "stored_events": events, "neural_mode": "disabled",
        "trust": "checksum_only_not_authenticated",
    }
    for key, value in checks.items():
        if type(result.get(key)) is not type(value) or result[key] != value:
            raise SmokeFailure("invalid checkpoint " + key)
    for key in ("journal_head", "manifest_sha256"):
        if type(result.get(key)) is not str or HEX_SHA256.fullmatch(result[key]) is None:
            raise SmokeFailure("invalid checkpoint " + key)
    if type(result.get("agent_id")) is not str or not result["agent_id"]:
        raise SmokeFailure("invalid checkpoint identity")
    if type(result.get("status")) is not str or not result["status"].startswith(
            "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO"):
        raise SmokeFailure("CLI reported a non-synthetic mode")
    for key in ("sim_time_ns", "world_revision", "genome_revision"):
        if type(result.get(key)) is not int or result[key] < 0:
            raise SmokeFailure("invalid checkpoint " + key)
    return result


def _integrity_check(db_path):
    if not db_path.is_file():
        raise SmokeFailure("fixture SQLite file is absent")
    # The URI is constructed exclusively from a TemporaryDirectory path.
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        response = conn.execute("PRAGMA integrity_check").fetchall()
    if response != [("ok",)]:
        raise SmokeFailure("SQLite integrity_check failed")


def _validate_batch(value, name):
    if type(value) is not int or not 1 <= value <= MAX_BATCH:
        raise SmokeFailure(f"{name} must be an integer within 1..{MAX_BATCH}")


def run_audit(*, first_ticks=3, second_ticks=2, timeout=45, runner=None):
    """Exercise CLI process boundaries using one *disposable* private journal.

    `runner` allows hermetic tests, not external journal/path selection. A
    healthy result contains only synthetic fixture metadata and no DB path.
    """
    _validate_batch(first_ticks, "first_ticks")
    _validate_batch(second_ticks, "second_ticks")
    if type(timeout) is not int or not 5 <= timeout <= 120:
        raise SmokeFailure("timeout must be an integer within 5..120")
    if runner is None:
        runner = subprocess.run
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    with tempfile.TemporaryDirectory(prefix="mojum-smoke-") as directory:
        db = Path(directory) / "fixture_PRIVATE.sqlite3"
        checkpoints = []
        for tick_batch in (first_ticks, second_ticks, 0):
            previous = checkpoints[-1] if checkpoints else None
            expected_ticks = ((previous["tick"] if previous else 0) + tick_batch)
            command = [sys.executable, "-m", "mojum.aquarium", "--db", str(db),
                       "--neural-mode", "disabled", "--ticks", str(tick_batch)]
            if previous is not None:
                command.extend(("--expected-head", previous["journal_head"]))
            try:
                proc = runner(command, cwd=str(ROOT), env=env,
                              capture_output=True, text=True, timeout=timeout,
                              check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SmokeFailure("Aquarium subprocess could not complete") from exc
            if proc.returncode != 0:
                # Do not forward child stderr: it may contain a disposable path
                # or internal fixture values.
                raise SmokeFailure("Aquarium CLI exited unsuccessfully")
            state = _checkpoint(proc.stdout, tick=expected_ticks, events=expected_ticks + 1)
            if previous is not None:
                for key in ("agent_id", "manifest_sha256", "genome_revision"):
                    if state[key] != previous[key]:
                        raise SmokeFailure("cold restart changed " + key)
                if state["world_revision"] < previous["world_revision"]:
                    raise SmokeFailure("world revision regressed")
                if state["sim_time_ns"] < previous["sim_time_ns"]:
                    raise SmokeFailure("virtual clock regressed")
                if tick_batch == 0 and state["journal_head"] != previous["journal_head"]:
                    raise SmokeFailure("zero-tick reopen changed durable journal head")
                if tick_batch > 0 and state["journal_head"] == previous["journal_head"]:
                    raise SmokeFailure("journal head did not advance after ticks")
            checkpoints.append(state)
        _integrity_check(db)
    return {
        "schema": SCHEMA, "result": "PASS", "fixture": "PUBLIC_SYNTHETIC",
        "process_invocations": 3, "first_ticks": first_ticks,
        "second_ticks": second_ticks, "final_tick": first_ticks + second_ticks,
        "stored_events": first_ticks + second_ticks + 1,
        "zero_tick_reopen_unchanged": True, "sqlite_integrity": "ok",
        "claim": "single-host_checksum-only_not_private-data-or-hardware-attestation",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-ticks", type=int, default=3)
    parser.add_argument("--second-ticks", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args(argv)
    try:
        report = run_audit(first_ticks=args.first_ticks,
                           second_ticks=args.second_ticks, timeout=args.timeout)
    except SmokeFailure as exc:
        print(json.dumps({"schema": SCHEMA, "result": "FAIL", "reason": str(exc)},
                         sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
