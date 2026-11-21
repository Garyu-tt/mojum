"""Actual R0.16 native v4+toy_v2 PRIVATE exporter => R0.15 offline Chrome.

This runs 10,003 REAL synthetic Aquarium frames on segmented source,
cold replays with native runtime, exports only 5 checked frames from a
read-only SQLite Backup API snapshot, and plays in actual headless Chrome
using immutable external viewer QA, never a patched/mock browser.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text("utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text("utf-8"))
SOURCE_VIEWER_SHA = "7752f510327f2a909d9bceb8caaa661358e0622e"


def options():
    room = Room(.32, .32)
    scene = ToyScene(
        width=.32, height=.32,
        toys=(Toy("inert.synthetic", .16, .16, .15, (42, 90, 174), .4),),
    )
    return {
        "neural_mode": "disabled", "exploration_mode": "goal_free",
        "developmental_policy": "local_cell_v4",
        "sensory_mode": "toy_probe_v2", "sensory_scene": scene,
        "room": room, "start": (.16, .16),
        "journal_backend": "segmented", "journal_segment_size": 4096,
    }


def rewrite(frames, footer):
    wire = "".join(
        json.dumps(f, sort_keys=True, ensure_ascii=False,
                   allow_nan=False, separators=(",", ":")) + "\n"
        for f in frames
    ).encode("utf-8")
    f = copy.deepcopy(footer)
    f["trace_integrity"]["sha256"] = hashlib.sha256(wire).hexdigest()
    f["trace_integrity"]["byte_length"] = len(wire)
    return wire.decode("utf-8") + json.dumps(
        f, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":")
    ) + "\n"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: qa_r016_epoch_export_browser.py viewer-qa-checkout")
    viewer = Path(sys.argv[1]).resolve()
    assert (viewer / "examples/aquarium_trace_viewer.html").is_file()
    sys.path.insert(0, str(viewer / "tests"))
    from smoke_real_chromium_viewer import (  # noqa: E402
        chrome_binary, instrumented_html, inspect_actual_browser,
        inspect_actual_mobile_chromedriver,
    )
    chrome = chrome_binary()
    html = (viewer / "examples/aquarium_trace_viewer.html").read_text("utf-8")
    opts = options()
    with tempfile.TemporaryDirectory(prefix="mojum-r016-real-source-") as work:
        folder = Path(work)
        source = folder / "segmented.synthetic.private.sqlite3"
        dest = folder / "private.epoch-slice.jsonl"
        with Aquarium(source, SEED, GRAPH, **opts) as aquarium:
            for k in range(10003):
                frame = aquarium.step()
                if k in (0, 9998, 9999, 10002):
                    assert frame["tick"] == k
            checkpoint = aquarium.checkpoint()
            total_events = aquarium.store.count()
            assert total_events == 10005, total_events
            assert checkpoint["tick"] == 10003
            assert checkpoint["epoch_index"] == 1
            sensory = checkpoint["toy_contact_state"]
            assert sensory["updates"] > 4000, sensory["updates"]
            independent_head = checkpoint["journal_head"]
            assert aquarium.store.verify_chain(
                external_expected_head=independent_head
            )["head"] == independent_head
        original_bytes = source.read_bytes()
        with Aquarium(source, SEED, GRAPH, **opts) as cold:
            assert cold.checkpoint() == checkpoint, "native full cold replay must agree"
            assert cold.store.count() == total_events
            assert cold.store.verify_chain(
                external_expected_head=independent_head
            )["head"] == independent_head
        metadata = export_private_trace(
            source, dest, SEED, GRAPH, allow_private_export=True,
            expected_journal_head=independent_head,
            start_tick=9998, frame_limit=5, **opts,
        )
        assert source.read_bytes() == original_bytes
        assert metadata["frames"] == 5
        assert metadata["first_tick"] == 9998
        assert metadata["next_tick"] == 10003
        assert metadata["event_count"] == 10005
        assert metadata["journal_head"] == independent_head
        if os.name == "posix":
            assert stat.S_IMODE(dest.stat().st_mode) == 0o600
        rows = [json.loads(x) for x in dest.read_text("utf-8").splitlines()]
        assert len(rows) == 6
        frames, footer = rows[:-1], rows[-1]
        assert [f["tick"] for f in frames] == [9998, 9999, 10000, 10001, 10002]
        assert footer["schema"] == "mojum.aquarium.checkpoint/2"
        assert footer["policy_schema"] == "mojum.developmental.local-cell/4"
        assert footer["epoch_index"] == 1 and footer["epoch_tick"] == 4
        assert footer["stored_events"] == total_events
        assert footer["epoch_boundaries"] == [{
            "schema": "mojum.aquarium.epoch-transition/1",
            "absolute_tick": 9999, "epoch_index": 1,
        }]
        assert all(f["toy_contact"]["schema"] == "mojum.aquarium.toy-contact/2"
                   for f in frames)
        for key in ("toy_contact_state", "neural_state", "developmental_state",
                    "genome_revision", "sensory_scene", "acquired_genome"):
            assert key not in footer
        initial = dest.read_text("utf-8")
        good = []
        for width, height, device in (
            (1280, 800, "desktop"), (390, 844, "mobile"),
        ):
            instrumented = instrumented_html(
                html, initial, expected_first_tick="9998 · эпоха 0:9998",
            )
            r = (inspect_actual_mobile_chromedriver(
                chrome, instrumented, width=width, height=height,
            ) if device == "mobile" else inspect_actual_browser(
                chrome, instrumented, width=width, height=height,
            ))
            assert r["first"]["tick"] == "9998 · эпоха 0:9998"
            assert r["second"]["tick"] == "9999 · эпоха 1:0"
            assert r["first"]["policy"] == "local_cell_v4 · эпохи 9999"
            assert r["first"]["brain"] == "UNIMPLEMENTED"
            assert "SHA-256 кадров совпадает" in r["second"]["source"]
            assert r["second"]["counter"] == "2 / 5"
            assert r["viewport"]["scrollWidth"] <= r["viewport"]["width"] + 2
            if device == "mobile":
                assert r["viewport"]["width"] == 390
            good.append({"viewport": device, "width": r["viewport"]["width"]})
        # Footer-only forgery preserves all real source frames and SHA.
        altered = copy.deepcopy(footer)
        altered["epoch_boundaries"] = []
        missing_marker = rewrite(frames, altered)
        fail = inspect_actual_browser(
            chrome, instrumented_html(
                html, missing_marker,
                expected_rejection="Отсутствует маркер перехода эпохи",
            ), width=1280, height=800,
        )
        assert fail["rejected"] and fail["controls_disabled"]
        # Frame forgery with a CORRECTLY recomputed SHA: same strict refusal.
        fake_frames = copy.deepcopy(frames)
        fake_frames[1]["developmental"]["learning"]["epoch_index"] = 0
        forged = rewrite(fake_frames, footer)
        fail = inspect_actual_browser(
            chrome, instrumented_html(
                html, forged,
                expected_rejection="Неверный абсолютный такт или эпоха v4",
            ), width=1280, height=800,
        )
        assert fail["rejected"] and fail["controls_disabled"]
        print(json.dumps({
            "status": "PASS_NATIVE_V4_SEGMENTED_SENSORY2_CHROME",
            "source_viewer_commit": SOURCE_VIEWER_SHA,
            "real_motor_frames": checkpoint["tick"],
            "real_journal_events": total_events,
            "real_measured_contact_updates": sensory["updates"],
            "source_unchanged": True,
            "source_cold_replay": True,
            "browser_positive": good,
            "browser_tamper_rejected": 2,
            "private_payload_uploaded": False,
            "release": "DRAFT_NO_MAIN_NO_REAL_DEVICE",
        }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
