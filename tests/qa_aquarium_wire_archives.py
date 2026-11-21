"""Generate disposable journals using the exact #112, #115 and #243 runtimes."""

import argparse
from contextlib import closing
import importlib.resources
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_wire import inspect_journal


def run(source, db, *options):
    result = subprocess.run(
        [sys.executable, "-m", "mojum.aquarium", "--db", str(db), "--ticks", "5", *options],
        cwd=source, env=dict(os.environ, PYTHONPATH=str(source / "src")),
        encoding="utf-8", capture_output=True, check=True, timeout=45,
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("source-a", "source-b", "source-guided"):
        parser.add_argument("--" + flag, type=Path, required=True)
    opts = parser.parse_args()
    cases = (
        (opts.source_a, ("--exploration-mode", "goal_free"),
         "legacy_coarse_v1", "mojum.developmental.explorer/1", None),
        (opts.source_a, ("--exploration-mode", "goal_free", "--motor-policy", "local_cell_v2"),
         "local_cell_v2_a_unreleased", "mojum.developmental.explorer.local-cell/2", None),
        (opts.source_b, ("--exploration-mode", "goal_free", "--developmental-policy", "local_cell_v2"),
         "local_cell_v2_b_unreleased", "mojum.developmental.local-cell/2", None),
        (opts.source_guided, ("--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
                              "--temporal-mode", "range_guided"),
         "motor_play_v1", "mojum.developmental.motor-play/1", "mojum.temporal.motor-play-range/1"),
    )
    data = importlib.resources.files("mojum").joinpath("data")
    defaults = [json.loads(data.joinpath(name).read_text(encoding="utf-8"))
                for name in ("genesis_seed.json", "first_loop.json")]
    with tempfile.TemporaryDirectory(prefix="mojum-wire-fixtures-") as scratch:
        for source, flags, expected_format, expected_schema, temporal in cases:
            db = Path(scratch) / (expected_format + ".sqlite3")
            checkpoint = run(source.resolve(), db, *flags)
            with closing(sqlite3.connect(db)) as conn:
                assert conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
            before = db.read_bytes()
            report = inspect_journal(db, external_expected_head=checkpoint["journal_head"])
            assert (report["format"], report["state_schema"], report["temporal_schema"]) == (
                expected_format, expected_schema, temporal,
            ), report
            assert report["frames"] == 5 and report["events"] == 6
            try:
                with Aquarium(db, *defaults):
                    raise AssertionError("foreign ABI admitted as a writable scripted journal")
            except AquariumError as exc:
                assert "mode mismatch" in str(exc), str(exc)
            assert db.read_bytes() == before, expected_format
            print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
