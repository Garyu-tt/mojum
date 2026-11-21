"""Generate disposable journals using the exact #112, #115 and #243 runtimes."""

import argparse
from contextlib import closing
import importlib.resources
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from mojum.aquarium import Aquarium, AquariumError, V2A_ARCHIVE_MODE, V2B_MODE, V2B_WIRE_MODE
from mojum.aquarium_wire import inspect_journal


def run(source, db, *options, ticks=5):
    result = subprocess.run(
        [sys.executable, "-m", "mojum.aquarium", "--db", str(db), "--ticks", str(ticks), *options],
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
        ("coarse-from-112", opts.source_a, ("--exploration-mode", "goal_free"),
         "legacy_coarse_v1", "mojum.developmental.explorer/1", None),
        ("coarse-from-115", opts.source_b, ("--exploration-mode", "goal_free"),
         "legacy_coarse_v1", "mojum.developmental.explorer/1", None),
        ("v2a-from-112", opts.source_a,
         ("--exploration-mode", "goal_free", "--motor-policy", "local_cell_v2"),
         "local_cell_v2_a_unreleased", "mojum.developmental.explorer.local-cell/2", None),
        ("v2b-from-115", opts.source_b,
         ("--exploration-mode", "goal_free", "--developmental-policy", "local_cell_v2"),
         "local_cell_v2_b_unreleased", "mojum.developmental.local-cell/2", None),
        ("guided-from-243", opts.source_guided,
         ("--motor-mode", "developmental_fixture", "--sensory-mode", "toy_probe",
          "--temporal-mode", "range_guided"),
         "motor_play_v1", "mojum.developmental.motor-play/1", "mojum.temporal.motor-play-range/1"),
    )
    refused_modes = {
        "legacy_coarse_v1": (V2A_ARCHIVE_MODE, V2B_MODE, V2B_WIRE_MODE),
        "local_cell_v2_a_unreleased": (V2B_MODE, V2B_WIRE_MODE),
        "local_cell_v2_b_unreleased": (V2A_ARCHIVE_MODE,),
    }
    data = importlib.resources.files("mojum").joinpath("data")
    defaults = [json.loads(data.joinpath(name).read_text(encoding="utf-8"))
                for name in ("genesis_seed.json", "first_loop.json")]
    with tempfile.TemporaryDirectory(prefix="mojum-wire-fixtures-") as scratch:
        for name, source, flags, expected_format, expected_schema, temporal in cases:
            db = Path(scratch) / (name + ".sqlite3")
            checkpoint = run(source.resolve(), db, *flags)
            with closing(sqlite3.connect(db)) as conn:
                assert conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
            before = db.read_bytes()
            report = inspect_journal(db, external_expected_head=checkpoint["journal_head"])
            assert (report["format"], report["state_schema"], report["temporal_schema"]) == (
                expected_format, expected_schema, temporal,
            ), report
            assert report["frames"] == 5 and report["events"] == 6
            for mode in ("scripted", *refused_modes.get(expected_format, ())):
                try:
                    with Aquarium(db, *defaults, motor_mode=mode):
                        raise AssertionError(f"{name} admitted under foreign mode {mode}")
                except AquariumError as exc:
                    assert "mode mismatch" in str(exc), str(exc)
                assert db.read_bytes() == before, (name, mode)
            if expected_format == "legacy_coarse_v1":
                # The pinned runtime opens SQLite writable even at zero ticks.
                # Replay a separate copy and preserve the original archive.
                replay_db = db.with_name(f"{db.stem}-replay.sqlite3")
                shutil.copyfile(db, replay_db)
                replayed = run(source.resolve(), replay_db, *flags, ticks=0)
                assert replayed == checkpoint, name
                assert inspect_journal(
                    replay_db, external_expected_head=checkpoint["journal_head"],
                ) == report, name
                assert db.read_bytes() == before, name
            print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
