"""Deterministic modern-format offline viewer control regression (#319).

The current viewer's actual inline JS is exercised using Node's stdlib VM,
without network, browser automation, or a real PRIVATE journal. Exporter and
SQLite integration are covered separately in test_private_trace_integrity.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "examples/aquarium_trace_viewer.html"

NODE_HARNESS = r"""
"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const {createHash, webcrypto} = require("node:crypto");
const {TextEncoder, TextDecoder} = require("node:util");
const assert = require("node:assert/strict");
const html = fs.readFileSync(process.argv[1], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(script, "viewer inline script missing");

const elements = new Map();
const timers = new Map();
let timerId = 0;
const make = id => ({
  id, value: "1", max: "0", disabled: false, checked: false,
  textContent: "", hidden: false, children: [], listeners: {}, files: [],
  classList: {toggle() {}},
  addEventListener(name, callback) {this.listeners[name] = callback;},
  replaceChildren() {this.children = [];},
  append(...items) {this.children.push(...items);},
  appendChild(item) {this.children.push(item);}
});
const get = id => {
  if (!elements.has(id)) {
    const e = make(id);
    if (id === "room") {
      e.width = 960; e.height = 600;
      e.getContext = () => new Proxy({}, {
        get(target, prop) {
          if (["fillRect", "strokeRect", "beginPath", "moveTo",
               "lineTo", "stroke", "arc", "fill", "fillText"].includes(prop))
            return () => {};
          return target[prop];
        },
        set(target, prop, value) {target[prop] = value; return true;}
      });
    }
    elements.set(id, e);
  }
  return elements.get(id);
};
const document = {
  hidden: false,
  listeners: {},
  getElementById: get,
  createElement: () => make("created"),
  addEventListener(name, callback) {this.listeners[name] = callback;}
};
vm.runInNewContext(script[1], {
  document, crypto: webcrypto, TextEncoder, TextDecoder,
  setInterval(callback, period) {
    assert.ok(period > 0, "invalid playback interval");
    const id = ++timerId;
    timers.set(id, {callback, period});
    return id;
  },
  clearInterval(id) {timers.delete(id);},
  fetch() {throw Error("viewer attempted network fetch");},
  WebSocket() {throw Error("viewer attempted network socket");},
  XMLHttpRequest() {throw Error("viewer attempted XMLHttpRequest");}
}, {timeout: 3000});

function frame(tick, blocked) {
  return {
    schema: "mojum.aquarium.frame/1", tick,
    world: {position: [1.2, 2.1], blocked},
    interoception: {heartbeat: {pulse: tick % 2 === 0}},
    developmental: {
      observation: tick === 4 ? {range_bucket_5cm_ewns: [0, 12, 5, 6]} : {},
      intent: {action: "x-"},
      learning: {total_updates: tick + 1}
    },
    brain: {status: tick === 6 ? "<img src=x onerror=alert(1)>" : "UNIMPLEMENTED"},
    toy_contact: {
      observation: {visible: true, range_bucket_10cm: 2},
      probe_intent: {requested: tick === 4},
      measured_contact: {contact: tick === 4, measured_deflection: 0.75},
      prediction_before: {predicted_deflection: 0.5, model_updates: 2}
    }
  };
}
function modernWire() {
  const frames = [frame(4, false), frame(5, true), frame(6, false)];
  const payload = frames.map(JSON.stringify).join("\n") + "\n";
  const proof = {
    schema: "mojum.aquarium.trace-frames-sha256/1",
    byte_length: Buffer.byteLength(payload),
    frame_count: frames.length,
    sha256: createHash("sha256").update(payload).digest("hex")
  };
  const footer = {
    schema: "mojum.aquarium.checkpoint/1",
    stored_events: 8,
    trace_range: {
      start_tick: 4, next_tick: 7,
      source_total_ticks: 7, has_more: false
    },
    trace_room: {width: 4, height: 4},
    export_status: "REPLAY_VERIFIED_SYNTHETIC_PRIVATE_NOT_AUTHENTICATED",
    trace_integrity: proof
  };
  return payload + JSON.stringify(footer) + "\n";
}
function click(id) {
  const target = get(id);
  assert.equal(typeof target.listeners.click, "function", "missing click: " + id);
  target.listeners.click.call(target);
}
function stepTimer() {
  assert.equal(timers.size, 1, "expected exactly one active timer");
  [...timers.values()][0].callback();
}
function tickState() {
  return {
    tick: get("tick").textContent,
    counter: get("counter").textContent,
    range: get("range").textContent,
    probe: get("probe").textContent,
    deflection: get("deflection").textContent,
    prediction: get("prediction").textContent,
    updates: get("learned").textContent,
    blocked: get("blocked").textContent,
    brain: get("brain").textContent,
    seek: get("seek").value,
    playLabel: get("play").textContent,
    activeTimers: timers.size
  };
}

(async () => {
  const wire = modernWire();
  const file = get("file");
  file.files = [{
    name: "synthetic.controls.private.jsonl",
    size: Buffer.byteLength(wire),
    arrayBuffer: async () => Uint8Array.from(Buffer.from(wire)).buffer
  }];
  await file.listeners.change.call(file);
  const loaded = tickState();
  loaded.status = get("status").textContent;
  loaded.enabled = !get("play").disabled;
  loaded.seekMax = get("seek").max;
  loaded.inputReusable = file.value;

  click("next");
  const next = tickState();
  click("prev");
  const prev = tickState();
  click("end");
  const end = tickState();
  click("play");
  const restart = tickState();
  const initialPeriod = [...timers.values()][0]?.period;
  stepTimer();
  const playing = tickState();
  click("play");
  const pause = tickState();

  get("speed").value = "4";
  click("play");
  const fastPeriod = [...timers.values()][0]?.period;
  get("speed").value = "2";
  get("speed").listeners.change();
  const changedPeriod = [...timers.values()][0]?.period;
  const speedTimers = timers.size;

  get("seek").value = "2";
  get("seek").listeners.input();
  const seek = tickState();

  click("play");
  stepTimer();
  stepTimer();
  const ended = tickState();
  click("play");
  document.hidden = true;
  document.listeners.visibilitychange();
  const hidden = tickState();
  click("start");
  const start = tickState();

  process.stdout.write(JSON.stringify({
    loaded, next, prev, end, restart, initialPeriod,
    playing, pause, fastPeriod, changedPeriod, speedTimers,
    seek, ended, hidden, start
  }));
})().catch(error => {console.error(error.stack || error); process.exitCode = 1;});
"""


def _run_node(node, script, *args):
    """Run Node with its documented UTF-8 stream encoding, never host locale."""
    completed = subprocess.run(
        [node, "-e", script, *map(str, args)],
        cwd=ROOT, capture_output=True, timeout=20, check=False,
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        completed.stdout.decode("utf-8", errors="strict"),
        completed.stderr.decode("utf-8", errors="strict"),
    )


class OfflineViewerControlsQA(unittest.TestCase):
    def _require_node_for_ci(self):
        node = shutil.which("node")
        if node is None:
            if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
                self.fail("Node required in CI for actual offline viewer playback QA")
            self.skipTest("Node unavailable: optional local viewer QA")
        return node

    def test_missing_node_must_fail_ci(self):
        with mock.patch("shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": ""}):
                with self.assertRaisesRegex(AssertionError, "Node required in CI"):
                    self._require_node_for_ci()

    def test_missing_node_may_skip_outside_ci(self):
        with mock.patch("shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}):
                with self.assertRaises(unittest.SkipTest):
                    self._require_node_for_ci()

    def test_node_output_must_be_valid_utf8(self):
        node = self._require_node_for_ci()
        with self.assertRaises(UnicodeDecodeError):
            _run_node(node, "process.stdout.write(Buffer.from([0xff]));")

    def test_verified_file_play_pause_seek_speed_and_numeric_telemetry(self):
        node = self._require_node_for_ci()
        run = _run_node(node, NODE_HARNESS, VIEWER)
        self.assertEqual(run.returncode, 0, run.stderr)
        values = json.loads(run.stdout)
        loaded = values["loaded"]
        self.assertTrue(loaded["enabled"])
        self.assertEqual(loaded["inputReusable"], "")
        self.assertEqual(loaded["seekMax"], "2")
        self.assertEqual(loaded["tick"], "4")
        self.assertEqual(loaded["counter"], "1 / 3")
        self.assertEqual(loaded["range"], "0 / 12 / 5 / 6 × 5 см")
        self.assertEqual(loaded["probe"], "Запрошена")
        self.assertEqual(loaded["deflection"], "0.75")
        self.assertEqual(loaded["prediction"], "0.5")
        self.assertEqual(loaded["updates"], "2")
        self.assertIn("SHA-256 кадров совпадает", loaded["status"])

        self.assertEqual(values["next"]["tick"], "5")
        self.assertEqual(values["next"]["blocked"], "Заблокировано")
        self.assertEqual(values["next"]["range"], "2 × 10 см")
        self.assertEqual(values["next"]["probe"], "Не запрошена")
        self.assertEqual(values["prev"]["tick"], "4")
        self.assertEqual(values["end"]["tick"], "6")
        self.assertEqual(values["end"]["brain"], "<img src=x onerror=alert(1)>")
        self.assertEqual(values["restart"]["tick"], "4")
        self.assertEqual(values["restart"]["activeTimers"], 1)
        self.assertEqual(values["initialPeriod"], 400)
        self.assertEqual(values["playing"]["tick"], "5")
        self.assertIn("Пауза", values["playing"]["playLabel"])
        self.assertEqual(values["pause"]["tick"], "5")
        self.assertEqual(values["pause"]["activeTimers"], 0)
        self.assertEqual(values["fastPeriod"], 100)
        self.assertEqual(values["changedPeriod"], 200)
        self.assertEqual(values["speedTimers"], 1)
        self.assertEqual(values["seek"]["tick"], "6")
        self.assertEqual(values["seek"]["activeTimers"], 0)
        self.assertEqual(values["ended"]["tick"], "6")
        self.assertEqual(values["ended"]["activeTimers"], 0)
        self.assertEqual(values["hidden"]["activeTimers"], 0)
        self.assertEqual(values["start"]["tick"], "4")


if __name__ == "__main__":
    unittest.main()
