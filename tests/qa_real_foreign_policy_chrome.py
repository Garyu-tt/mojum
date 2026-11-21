"""Actual Chrome read-only v2B/v3B observer smoke from immutable peer trees.

Generating programs run in their OWN Python processes + own pinned source
checkouts. Do not import foreign classes into the viewer runtime, modify
peer SQLite history, or assert v2A and v2B writable compatibility.
All PRIVATE files are synthetic in a temporary test directory only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from smoke_real_chromium_viewer import (
    VIEWER, chrome_binary, inspect_actual_browser,
    inspect_actual_mobile_chromedriver, instrumented_html,
)

V2B_HEAD = "b755da530634c0d6b8866107e7b4e9e325839175"
V3B_HEAD = "8b7b12a1ca3fc89067671c8a63f82c0f0a752e31"
CONTRACTS = (
    ("v2b", "local_cell_v2", "mojum.developmental.local-cell/2",
     "mojum.developmental.local-cell.outcome/2",
     "local_cell_v2B · ячейки 4 см"),
    ("v3b", "local_cell_v3", "mojum.developmental.local-cell/3",
     "mojum.developmental.local-cell.outcome/3",
     "local_cell_v3B · ограниченная память"),
)

# Script runs from the SEPARATE immutable original foreign-source checkout.
# It uses fresh disposable SQLite and reopens it under its original policy.
GENERATE = r'''
import hashlib
import json
from pathlib import Path
import sys
import tempfile

tree = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
mode = sys.argv[3]
sys.path.insert(0, str(tree / "src"))
from mojum.aquarium import Aquarium

seed = json.loads((tree / "examples/genesis_seed.json").read_text("utf-8"))
graph = json.loads((tree / "examples/first_loop.json").read_text("utf-8"))
with tempfile.TemporaryDirectory(prefix="foreign-abi-source-") as scratch:
    db = Path(scratch) / "synthetic.private.sqlite3"
    opts = dict(exploration_mode="goal_free", developmental_policy=mode)
    with Aquarium(db, seed, graph, **opts) as source:
        first = [source.step() for _ in range(3)]
        checkpoint = source.checkpoint()
        trusted_head = checkpoint["journal_head"]
        assert source.store.count() == 4
    with Aquarium(db, seed, graph, **opts) as cold:
        assert cold.checkpoint() == checkpoint, "foreign original cold replay mismatch"
        assert cold.store.count() == 4
        cold.store.verify_chain(external_expected_head=trusted_head)
        items = cold.store.dataset(include_nonpublic=True, limit=4)["projection"]["items"]
        frames = [
            item["event"]["payload"] for item in items
            if item["event"]["topic"] == "aquarium.frame"
        ]
        assert frames == first, "original frames differ after foreign replay"
        assert len(frames) == 3
        for frame in frames:
            assert frame["developmental"]["motor_capability"] == "SYNTHETIC_POINT_ROOM_ONLY"
            assert frame["physical_model"] == "NONE_FIXTURE_ONLY"
            assert frame["brain"]["status"] == "UNIMPLEMENTED"
        schema = cold.checkpoint()["developmental_state"]["schema"]
        assert frames[0]["developmental"]["schema"] == schema
    wire = "".join(
        json.dumps(frame, ensure_ascii=False, sort_keys=True, allow_nan=False,
                   separators=(",", ":")) + "\n" for frame in frames
    ).encode("utf-8")
    footer = {
        "schema": "mojum.aquarium.checkpoint/1",
        "stored_events": 4,
        "tick": checkpoint["tick"],
        "journal_head": trusted_head,
        "policy_schema": schema,
        "trace_range": {"start_tick": 0, "next_tick": 3,
                        "has_more": False, "source_total_ticks": 3},
        "trace_integrity": {
            "schema": "mojum.aquarium.trace-frames-sha256/1",
            "sha256": hashlib.sha256(wire).hexdigest(),
            "byte_length": len(wire),
            "frame_count": 3,
        },
        "export_status": "EXTERNAL_PEER_SYNTHETIC_REPLAY_NOT_AUTHENTICATED",
    }
    # QA serialization from actual replay-checked frames. The source
    # foreign branch is NOT claimed to contain this exporter feature.
    target.write_bytes(wire + (
        json.dumps(footer, ensure_ascii=False, sort_keys=True,
                   allow_nan=False, separators=(",", ":")) + "\n"
    ).encode("utf-8"))
'''


def generate_foreign_trace(checkout, target, policy):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(checkout / "src")
    result = subprocess.run(
        [sys.executable, "-c", GENERATE, str(checkout), str(target), policy],
        cwd=checkout, env=env, capture_output=True, text=True, timeout=55,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            "foreign synthetic generator rejected source: " + result.stderr[-3000:]
        )
    assert target.exists(), "foreign program failed to produce test fixture"
    rows = [json.loads(x) for x in target.read_text("utf-8").splitlines()]
    assert len(rows) == 4
    return rows


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: qa_real_foreign_policy_chrome.py checkout-v2b checkout-v3b")
    checkouts = (Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    for path in checkouts:
        assert (path / "src/mojum/aquarium.py").is_file(), "missing pinned source tree"
    chrome = chrome_binary()
    reports = []
    html = VIEWER.read_text("utf-8")
    with tempfile.TemporaryDirectory(prefix="mojum-foreign-browser-") as scratch:
        for (tag, mode, schema, outcome_schema, label), checkout in zip(
            CONTRACTS, checkouts
        ):
            path = Path(scratch) / (tag + ".private.synthetic.jsonl")
            records = generate_foreign_trace(checkout, path, mode)
            footer, frames = records[-1], records[:-1]
            assert footer["policy_schema"] == schema
            assert footer["trace_integrity"]["frame_count"] == 3
            assert all(frame["developmental"]["schema"] == schema for frame in frames)
            assert all(
                frame["developmental"]["learning"]["schema"] == outcome_schema
                for frame in frames
            )
            raw = path.read_text("utf-8")
            for width, height, variant in (
                (1280, 800, "desktop"),
                (390, 844, "mobile"),
            ):
                instrumented = instrumented_html(html, raw)
                observed = (
                    inspect_actual_mobile_chromedriver(
                        chrome, instrumented, width=width, height=height
                    ) if variant == "mobile" else inspect_actual_browser(
                        chrome, instrumented, width=width, height=height
                    )
                )
                first, second, viewport = (
                    observed["first"], observed["second"], observed["viewport"]
                )
                assert first["tick"] == "0" and second["tick"] == "1"
                assert first["policy"] == label, (tag, variant, first["policy"])
                assert first["action"] == frames[0]["developmental"]["intent"]["action"]
                assert first["brain"] == "UNIMPLEMENTED"
                assert second["counter"] == "2 / 3"
                assert "SHA-256 кадров совпадает" in second["source"]
                assert viewport["canvasWidth"] >= 100
                assert viewport["canvasHeight"] >= 60
                assert viewport["scrollWidth"] <= viewport["width"] + 2
                if variant == "mobile":
                    assert viewport["width"] == 390
                reports.append({
                    "foreign_abi": tag,
                    "expected_dev_schema": schema,
                    "expected_learning_schema": outcome_schema,
                    "actual_browser": True,
                    "viewport": variant,
                    "css_width": viewport["width"],
                    "displayed_policy": first["policy"],
                    "digest_verified": True,
                    "synthetic_cold_replay": True,
                })
    assert len(reports) == 4
    print(json.dumps({
        "status": "PASS_REAL_PEER_CHROME_READONLY",
        "contracts": [V2B_HEAD, V3B_HEAD],
        "report": reports,
        "privacy": "synthetic source-only, no uploaded PRIVATE artifacts or remote viewer",
        "release_status": "BLOCKED_NO_WRITABLE_ABI_PROMOTION",
    }, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
