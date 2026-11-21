"""Offline trace viewer playback regression against its actual JavaScript.

This deliberately uses a disposable, explicitly opted-in *legacy* synthetic
JSONL fixture, not a PRIVATE journal or mock implementation of playback.
The separate trace-integrity QA verifies producer SHA-256 and strict import.
Node runs only as an optional test harness, never as a viewer dependency.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "examples/aquarium_trace_viewer.html"

NODE_HARNESS = r"""
"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const {TextDecoder, TextEncoder} = require("node:util");
const html = fs.readFileSync(process.argv[1], "utf8");
const trace = fs.readFileSync(process.argv[2], "utf8");
const match = html.match(/<script>([\s\S]*?)<\/script>/);
if (!match) throw Error("viewer script not found");

const nodes = new Map();
const timers = new Map();
const docListeners = new Map();
let timerSerial = 0;
function element(id) {
  if (!nodes.has(id)) {
    const node = {
      id, disabled: false, checked: false, hidden: false,
      value: "0", max: "0", textContent: "", className: "",
      listeners: {}, files: [], children: [],
      classList: {toggle(){}},
      addEventListener(type, callback) {this.listeners[type] = callback;},
      append(...items) {this.children.push(...items);},
      appendChild(item) {this.children.push(item);},
      replaceChildren() {this.children = [];}
    };
    if (id === "room") {
      node.width = 960;
      node.height = 600;
      node.getContext = () => new Proxy({}, {
        get(obj, prop) {
          return ["fillRect", "strokeRect", "beginPath", "moveTo",
            "lineTo", "stroke", "arc", "fill", "fillText"].includes(prop)
            ? () => {} : obj[prop];
        },
        set(obj, prop, value) {obj[prop] = value; return true;}
      });
    }
    nodes.set(id, node);
  }
  return nodes.get(id);
}
const document = {
  hidden: false,
  getElementById: element,
  createElement: () => ({
    children: [], textContent: "", className: "",
    append(...items) {this.children.push(...items);}
  }),
  addEventListener: (type, callback) => docListeners.set(type, callback)
};
vm.runInNewContext(match[1], {
  document, Math, Number, String, Array, Set, Map, JSON, Promise,
  TextDecoder, TextEncoder,
  setInterval(callback, delay) {
    if (!Number.isFinite(delay) || delay <= 0) throw Error("invalid playback interval");
    const id = ++timerSerial;
    timers.set(id, {callback, delay});
    return id;
  },
  clearInterval(id) {timers.delete(id);},
  fetch() {throw Error("viewer tried to access the network");},
  WebSocket() {throw Error("viewer tried to open a network socket");}
}, {timeout: 3000});

function fire() {
  if (timers.size !== 1) throw Error("expected one active playback timer");
  [...timers.values()][0].callback();
}
function click(id) {
  const fn = element(id).listeners.click;
  if (!fn) throw Error("missing click listener on " + id);
  fn.call(element(id));
}
function change(id, eventName) {
  const fn = element(id).listeners[eventName];
  if (!fn) throw Error("missing listener on " + id);
  return fn.call(element(id));
}
function sample() {
  return {
    tick: element("tick").textContent,
    counter: element("counter").textContent,
    seek: element("seek").value,
    playLabel: element("play").textContent,
    playDisabled: element("play").disabled,
    timerCount: timers.size,
    timerDelay: timers.size ? [...timers.values()][0].delay : null
  };
}
async function loadSynthetic() {
  element("allow-legacy").checked = true;
  const bytes = Buffer.from(trace, "utf8");
  const input = element("file");
  input.files = [{
    name: "ephemeral.synthetic.legacy.jsonl", size: bytes.length,
    arrayBuffer: async () => Uint8Array.from(bytes).buffer
  }];
  await change("file", "change");
}
(async () => {
  element("speed").value = "1"; // mirror actual HTML's first <option>
  const states = {};
  states.initial = sample();
  await loadSynthetic();
  states.loaded = sample();
  click("next"); states.next = sample();
  click("prev"); states.prev = sample();
  element("seek").value = "2";
  change("seek", "input"); states.seek = sample();

  click("play"); states.play = sample();
  element("speed").value = "2";
  change("speed", "change"); states.speed = sample();
  fire(); states.finished = sample();
  click("play"); states.restarted = sample();
  click("play"); states.paused = sample();

  click("end"); states.end = sample();
  click("next"); states.nextAtEnd = sample();
  click("start"); states.start = sample();
  click("prev"); states.prevAtStart = sample();

  click("play");
  document.hidden = true;
  docListeners.get("visibilitychange")();
  states.hidden = sample();
  document.hidden = false;

  click("play");
  const input = element("file");
  input.files = [{
    name: "too-large.jsonl", size: 24 * 1024 * 1024 + 1,
    arrayBuffer: async () => {throw Error("oversize file was read");}
  }];
  await change("file", "change");
  states.rejectedImport = sample();
  states.rejectedStatus = element("status").textContent;

  process.stdout.write(JSON.stringify(states) + "\n");
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exit(1);
});
"""


class OfflineViewerPlaybackTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node unavailable; CI should run this test")
    def test_actual_viewer_playback_navigation_and_cancellation(self):
        # Four sequential public-format synthetic frames; no SQLite or private
        # user content. The trace is clearly legacy and requires explicit opt-in.
        frames = [
            {
                "schema": "mojum.aquarium.frame/1",
                "tick": tick,
                "world": {
                    "position": [1.0 + tick * 0.1, 2.0],
                    "blocked": tick == 2,
                },
                "interoception": {"heartbeat": {"pulse": tick % 2 == 0}},
                "brain": {"status": "UNIMPLEMENTED"},
            }
            for tick in range(4)
        ]
        footer = {"schema": "mojum.aquarium.checkpoint/1", "stored_events": 5}
        wire = "".join(json.dumps(item, separators=(",", ":")) + "\n"
                       for item in [*frames, footer])
        with tempfile.TemporaryDirectory(prefix="mojum-viewer-playback-") as tmp:
            file = Path(tmp) / "ephemeral.synthetic.jsonl"
            file.write_text(wire, encoding="utf-8")
            done = subprocess.run(
                [shutil.which("node"), "-e", NODE_HARNESS, str(VIEWER), str(file)],
                cwd=ROOT, encoding="utf-8", capture_output=True,
                timeout=20, check=False,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        states = json.loads(done.stdout)

        self.assertTrue(states["initial"]["playDisabled"])
        self.assertEqual(states["initial"]["timerCount"], 0)
        self.assertEqual(states["loaded"]["tick"], "0")
        self.assertFalse(states["loaded"]["playDisabled"])
        self.assertEqual(states["loaded"]["counter"], "1 / 4")
        self.assertEqual(states["next"]["tick"], "1")
        self.assertEqual(states["prev"]["tick"], "0")
        self.assertEqual(states["seek"]["tick"], "2")
        self.assertEqual(states["seek"]["seek"], "2")

        self.assertEqual(states["play"]["timerCount"], 1)
        self.assertEqual(states["play"]["timerDelay"], 400)
        self.assertIn("Пауза", states["play"]["playLabel"])
        self.assertEqual(states["speed"]["timerCount"], 1)
        self.assertEqual(states["speed"]["timerDelay"], 200)
        self.assertEqual(states["speed"]["tick"], "2")
        self.assertEqual(states["finished"]["tick"], "3")
        self.assertEqual(states["finished"]["timerCount"], 0)
        self.assertEqual(states["restarted"]["tick"], "0")
        self.assertEqual(states["restarted"]["timerCount"], 1)
        self.assertEqual(states["paused"]["timerCount"], 0)
        self.assertIn("Воспроизвести", states["paused"]["playLabel"])

        self.assertEqual(states["end"]["tick"], "3")
        self.assertEqual(states["nextAtEnd"]["tick"], "3")
        self.assertEqual(states["start"]["tick"], "0")
        self.assertEqual(states["prevAtStart"]["tick"], "0")
        self.assertEqual(states["hidden"]["timerCount"], 0)
        self.assertEqual(states["rejectedImport"]["timerCount"], 0)
        self.assertTrue(states["rejectedImport"]["playDisabled"])
        self.assertIn("24 МиБ", states["rejectedStatus"])


if __name__ == "__main__":
    unittest.main()
