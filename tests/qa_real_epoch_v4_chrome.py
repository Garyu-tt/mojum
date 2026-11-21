"""Real original-source v4 9998 -> 9999 SQLite epoch viewer acceptance.

No migration, writes to user databases, fabricated in-memory-only frames,
main mutation, claims of a signed JSONL, or any physical motor capability.
All source data is temporary synthetic PRIVATE SQLite in GitHub Actions.
"""
from __future__ import annotations

import copy
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

ORIGINAL_V4_SHA = "51d21f77b887f72cb68960a194e529d6073e3187"
POLICY_SCHEMA = "mojum.developmental.local-cell/4"
LEARNING_SCHEMA = "mojum.developmental.local-cell.outcome/4"
EPOCH_SCHEMA = "mojum.aquarium.epoch-transition/1"
EPOCH_TICKS = 9999
SEGMENT_FIRST = 9998
SEGMENT_END = 10003

GENERATE = r'''
import hashlib
import json
from pathlib import Path
import sys
import tempfile

tree = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(tree / "src"))
from mojum.aquarium import Aquarium
from mojum.epoch_sense_v2 import EPOCH_TICKS
from mojum.cell_policy_v4 import SCHEMA as POLICY, LEARNING_SCHEMA as LEARNED

seed = json.loads((tree / "examples/genesis_seed.json").read_text("utf-8"))
graph = json.loads((tree / "examples/first_loop.json").read_text("utf-8"))
opts = dict(exploration_mode="goal_free", developmental_policy="local_cell_v4",
            sensory_mode="none", neural_mode="disabled")
with tempfile.TemporaryDirectory(prefix="original-v4-epoch-source-") as tmp:
    db = Path(tmp) / "synthetic.private.sqlite3"
    with Aquarium(db, seed, graph, **opts) as first:
        for tick in range(10003):
            payload = first.step()
            assert payload["tick"] == tick
            if tick in (9998, 9999, 10000, 10002):
                assert payload["developmental"]["observation"]["absolute_tick"] == tick
                assert payload["developmental"]["observation"]["epoch_index"] == tick // EPOCH_TICKS
                assert payload["developmental"]["observation"]["epoch_tick"] == tick % EPOCH_TICKS
        checkpoint = first.checkpoint()
        expected_count = 10003 + 1 + 1  # bootstrap + one durable epoch marker
        assert first.store.count() == expected_count
        assert checkpoint["tick"] == 10003
        assert checkpoint["schema"] == "mojum.aquarium.checkpoint/2"
        assert checkpoint["epoch_index"] == 1 and checkpoint["epoch_tick"] == 4
        marker = first.store.event_at(10001)["event"]
        assert marker["topic"] == "aquarium.epoch"
        assert marker["event_id"] == "epoch-00000001"
        assert marker["payload"] == {
            "schema": "mojum.aquarium.epoch-transition/1",
            "absolute_tick": 9999, "epoch_index": 1,
            "previous_epoch_index": 0, "epoch_tick": 0,
            "source": "explicit_bounded_epoch_rollover_without_reset",
        }
        assert marker["visibility"] == "PRIVATE"
        assert marker["observed_at_ns"] == 9999 * first.dt_ns
        trusted_head = checkpoint["journal_head"]
    # Force the real 10k+ event replay with NO HMAC shortcut. Verify source
    # chain against the independently captured original head, not file digest.
    with Aquarium(db, seed, graph, **opts) as cold:
        assert cold.checkpoint() == checkpoint
        assert cold.store.count() == expected_count
        assert cold.store.verify_chain(external_expected_head=trusted_head)["head"] == trusted_head
        source_frames = []
        for tick in range(9998, 10003):
            seq = tick + 2 + (1 if tick >= EPOCH_TICKS else 0)
            event = cold.store.event_at(seq)["event"]
            assert event["topic"] == "aquarium.frame"
            assert event["visibility"] == "PRIVATE"
            frame = event["payload"]
            assert frame["schema"] == "mojum.aquarium.frame/1"
            assert frame["tick"] == tick
            assert frame["developmental"]["schema"] == POLICY
            assert frame["developmental"]["learning"]["schema"] == LEARNED
            assert frame["brain"]["status"] == "UNIMPLEMENTED"
            assert frame["physical_model"] == "NONE_FIXTURE_ONLY"
            assert frame["developmental"]["motor_capability"] == "SYNTHETIC_POINT_ROOM_ONLY"
            source_frames.append(frame)
        checked_marker = cold.store.event_at(10001)["event"]
        assert checked_marker == marker
    wire = "".join(
        json.dumps(f, sort_keys=True, ensure_ascii=False,
                   allow_nan=False, separators=(",", ":")) + "\n"
        for f in source_frames
    ).encode("utf-8")
    footer = {
        "schema": "mojum.aquarium.checkpoint/2",
        "agent_id": checkpoint["agent_id"],
        "tick": checkpoint["tick"], "epoch_index": checkpoint["epoch_index"],
        "epoch_tick": checkpoint["epoch_tick"],
        "journal_head": trusted_head, "stored_events": expected_count,
        "policy_schema": POLICY,
        "trace_range": {"start_tick": 9998, "next_tick": 10003,
                        "has_more": False, "source_total_ticks": 10003},
        "epoch_boundaries": [{
            "schema": checked_marker["payload"]["schema"],
            "absolute_tick": checked_marker["payload"]["absolute_tick"],
            "epoch_index": checked_marker["payload"]["epoch_index"],
        }],
        "trace_integrity": {
            "schema": "mojum.aquarium.trace-frames-sha256/1",
            "sha256": hashlib.sha256(wire).hexdigest(),
            "byte_length": len(wire), "frame_count": len(source_frames),
        },
        "export_status": "EXPERIMENTAL_V4_SYNTHETIC_REPLAY_NOT_AUTHENTICATED",
    }
    # Source #132 is NOT claimed to ship this trace exporter.
    # This is an explicit bounded QA serializer of verified source events,
    # not a HMAC-authenticated public record or a snapshot of brain weights.
    target.write_bytes(wire + (
        json.dumps(footer, sort_keys=True, ensure_ascii=False,
                   allow_nan=False, separators=(",", ":")) + "\n"
    ).encode("utf-8"))
'''


def synthetic_original_source(checkout, path):
    env = dict(os.environ, PYTHONPATH=str(checkout / "src"))
    result = subprocess.run(
        [sys.executable, "-c", GENERATE, str(checkout), str(path)],
        cwd=checkout, env=env, check=False, capture_output=True, text=True,
        timeout=260,
    )
    if result.returncode:
        raise AssertionError("original pinned v4 source failed: " + result.stderr[-2500:])
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert len(rows) == SEGMENT_END - SEGMENT_FIRST + 1
    return rows


def serialize(frames, footer):
    wire = "".join(
        json.dumps(frame, ensure_ascii=False, sort_keys=True,
                   allow_nan=False, separators=(",", ":")) + "\n"
        for frame in frames
    ).encode("utf-8")
    new_footer = copy.deepcopy(footer)
    new_footer["trace_integrity"]["sha256"] = hashlib.sha256(wire).hexdigest()
    new_footer["trace_integrity"]["byte_length"] = len(wire)
    new_footer["trace_integrity"]["frame_count"] = len(frames)
    return wire.decode("utf-8") + json.dumps(
        new_footer, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(",", ":")
    ) + "\n"


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: qa_real_epoch_v4_chrome.py original-v4-checkout")
    checkout = Path(sys.argv[1]).resolve()
    assert (checkout / "src/mojum/cell_policy_v4.py").is_file()
    chrome = chrome_binary()
    original = VIEWER.read_text("utf-8")
    with tempfile.TemporaryDirectory(prefix="mojum-epoch-v4-viewer-") as tmp:
        dest = Path(tmp) / "v4.boundary.private.synthetic.jsonl"
        rows = synthetic_original_source(checkout, dest)
        frames, footer = rows[:-1], rows[-1]
        assert [f["tick"] for f in frames] == list(range(SEGMENT_FIRST, SEGMENT_END))
        assert footer["policy_schema"] == POLICY_SCHEMA
        assert footer["epoch_boundaries"] == [
            {"schema": EPOCH_SCHEMA, "absolute_tick": EPOCH_TICKS,
             "epoch_index": 1}
        ]
        assert footer["stored_events"] == 10005
        report = []
        for width, height, device in ((1280, 800, "desktop"), (390, 844, "mobile")):
            page = instrumented_html(original, dest.read_text("utf-8"))
            result = (
                inspect_actual_mobile_chromedriver(
                    chrome, page, width=width, height=height
                ) if device == "mobile" else inspect_actual_browser(
                    chrome, page, width=width, height=height
                )
            )
            first, second, viewport = result["first"], result["second"], result["viewport"]
            assert first["tick"] == "9998 · эпоха 0:9998", first["tick"]
            assert second["tick"] == "9999 · эпоха 1:0", second["tick"]
            assert first["policy"] == "local_cell_v4 · эпохи 9999"
            assert first["brain"] == "UNIMPLEMENTED"
            assert first["action"] == frames[0]["developmental"]["intent"]["action"]
            assert second["counter"] == "2 / 5"
            assert "SHA-256 кадров совпадает" in second["source"]
            assert viewport["scrollWidth"] <= viewport["width"] + 2
            assert viewport["canvasWidth"] >= 100 and viewport["canvasHeight"] >= 60
            if device == "mobile":
                assert viewport["width"] == 390
            report.append({"viewport": device, "width": viewport["width"],
                           "first_tick": first["tick"], "second_tick": second["tick"],
                           "policy": first["policy"], "sha256_verified": True})
        # All deliberately forged files recompute exact SHA-256, so Chrome
        # must reject on ABI/causality/epoch marker, not digest mismatch.
        mutated = []
        wrong_marker = copy.deepcopy(footer)
        wrong_marker["epoch_boundaries"] = []
        mutated.append(("missing_committed_epoch_marker",
                        serialize(frames, wrong_marker),
                        "Отсутствует маркер перехода эпохи"))
        wrong_epoch = copy.deepcopy(frames)
        wrong_epoch[1]["developmental"]["observation"]["epoch_index"] = 0
        mutated.append(("lying_epoch_index",
                        serialize(wrong_epoch, footer),
                        "Неверный абсолютный такт или эпоха v4"))
        wrong_tick = copy.deepcopy(frames)
        wrong_tick[1]["developmental"]["intent"]["epoch_tick"] = 9999
        mutated.append(("wrapped_intent_epoch",
                        serialize(wrong_tick, footer),
                        "Неверный абсолютный такт или эпоха v4"))
        wrong_footer = copy.deepcopy(footer)
        wrong_footer["schema"] = "mojum.aquarium.checkpoint/1"
        mutated.append(("unversioned_checkpoint",
                        serialize(frames, wrong_footer),
                        "Нет проверенного checkpoint/2 и сведений эпохи v4"))
        wrong_sensor = copy.deepcopy(frames)
        wrong_sensor[0]["developmental"]["observation"]["schema"] = (
            "mojum.developmental.proprioception/1"
        )
        mutated.append(("old_sense_wearing_v4",
                        serialize(wrong_sensor, footer),
                        "Неверный абсолютный такт или эпоха v4"))
        for name, source, reason in mutated:
            result = inspect_actual_browser(
                chrome, instrumented_html(
                    original, source, expected_rejection=reason
                ), width=1280, height=800
            )
            assert result["rejected"] is True
            assert result["controls_disabled"] is True
            assert result["displayed_policy"] == "—", name
        print(json.dumps({
            "status": "PASS_REAL_V4_EPOCH_CHROME",
            "origin_immutable_sha": ORIGINAL_V4_SHA,
            "actual_source_tick_count": footer["tick"],
            "private_event_count": footer["stored_events"],
            "epoch_boundary": footer["epoch_boundaries"][0]["absolute_tick"],
            "positive": report,
            "negative_cases": len(mutated),
            "real_source_cold_replay": True,
            "authenticated_viewer_source": False,
            "release": "BLOCKED_EXPERIMENTAL_READ_ONLY",
        }, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
