"""Offline viewer navigation regression against real replay-verified SQLite export.

The viewer source is executed unmodified in a deterministic Node stdlib DOM.
No network, browser server, secrets, real PRIVATE histories or wall-clock timers.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "examples" / "aquarium_trace_viewer.html"
SEED = json.loads((ROOT / "src/mojum/data/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "src/mojum/data/first_loop.json").read_text(encoding="utf-8"))


NODE = r"""
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {TextEncoder, TextDecoder} = require("node:util");
const html = fs.readFileSync(process.argv[1], "utf8");
const trace = fs.readFileSync(process.argv[2]);
const inline = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(inline, "the actual offline viewer inline JS must be present");

const timers = new Map();
let timerIndex = 0;
function schedule(fn, delay) {
  assert.ok(delay > 0 && Number.isFinite(delay), "finite playback cadence");
  const id = ++timerIndex;
  timers.set(id, {fn, delay});
  return id;
}
function cancel(id) { timers.delete(id); }
function fire() {
  assert.equal(timers.size, 1, "exactly one playback interval");
  Array.from(timers.values())[0].fn();
}
const elements = new Map();
const document = {
  hidden: false,
  listeners: {},
  getElementById(id) {
    if (!elements.has(id)) {
      const e = {
        id, disabled: false, checked: false, hidden: false,
        textContent: "", value: id === "speed" ? "1" : "0", max: "0",
        files: [], children: [], listeners: {},
        classList: {toggle() {}},
        addEventListener(event, fn) { this.listeners[event] = fn; },
        append(...items) { this.children.push(...items); },
        appendChild(item) { this.children.push(item); },
        replaceChildren() { this.children = []; },
      };
      if (id === "room") {
        e.width = 960; e.height = 600;
        e.getContext = () => new Proxy({}, {
          get(obj, prop) {
            if (["fillRect", "strokeRect", "beginPath", "moveTo", "lineTo",
                 "stroke", "arc", "fill", "fillText"].includes(prop)) return () => {};
            return obj[prop];
          },
          set(obj, prop, value) { obj[prop] = value; return true; },
        });
      }
      elements.set(id, e);
    }
    return elements.get(id);
  },
  createElement() {
    return {className: "", textContent: "", children: [],
      append(...items) { this.children.push(...items); }};
  },
  addEventListener(event, fn) { this.listeners[event] = fn; },
};
const get = id => document.getElementById(id);
vm.runInNewContext(inline[1], {
  document, crypto: require("node:crypto").webcrypto, TextDecoder, TextEncoder,
  setInterval: schedule, clearInterval: cancel,
  fetch() { throw Error("network forbidden"); },
  XMLHttpRequest: class { constructor() { throw Error("XHR forbidden"); } },
  WebSocket: class { constructor() { throw Error("socket forbidden"); } },
}, {timeout: 3000});

async function load(bytes, name = "verified.private.jsonl") {
  const input = get("file");
  input.files = [{
    name, size: bytes.length,
    async arrayBuffer() {
      const copy = Uint8Array.from(bytes);
      return copy.buffer.slice(copy.byteOffset, copy.byteOffset + copy.byteLength);
    },
  }];
  await input.listeners.change.call(input);
  assert.equal(input.value, "", "reselecting same filename must stay possible");
}
function click(id) { get(id).listeners.click(); }
function check(tick, counter) {
  assert.equal(get("tick").textContent, String(tick));
  assert.equal(get("counter").textContent, counter);
}

(async () => {
  await load(trace);
  assert.match(get("status").textContent, /SHA-256 кадров совпадает/);
  assert.equal(get("play").disabled, false);
  assert.equal(get("seek").max, "2");
  check(0, "1 / 3");

  click("prev"); check(0, "1 / 3"); // no underflow
  click("next"); check(1, "2 / 3");
  click("end"); check(2, "3 / 3");
  click("next"); check(2, "3 / 3"); // no overflow
  click("start"); check(0, "1 / 3");
  get("seek").value = "2"; get("seek").listeners.input();
  check(2, "3 / 3");
  get("seek").value = "-50"; get("seek").listeners.input();
  check(0, "1 / 3"); // clamp an invalid programmatic slider position

  click("play");
  assert.equal(timers.size, 1);
  assert.equal(Array.from(timers.values())[0].delay, 400);
  assert.match(get("play").textContent, /Пауза/);
  fire(); check(1, "2 / 3");
  get("speed").value = "2"; get("speed").listeners.change();
  assert.equal(timers.size, 1, "speed changes replace, not duplicate timers");
  assert.equal(Array.from(timers.values())[0].delay, 200);
  fire(); check(2, "3 / 3");
  assert.equal(timers.size, 0, "reaching final frame must stop playback");
  assert.match(get("play").textContent, /Воспроизвести/);

  click("play"); check(0, "1 / 3"); // replay from final frame
  assert.equal(timers.size, 1);
  document.hidden = true;
  document.listeners.visibilitychange();
  assert.equal(timers.size, 0, "backgrounded viewer must pause");
  assert.match(get("play").textContent, /Воспроизвести/);
  document.hidden = false;

  click("play"); assert.equal(timers.size, 1);
  const input = get("file");
  input.files = [{
    name: "oversized.private.jsonl", size: 25 * 1024 * 1024,
    async arrayBuffer() { throw Error("oversized file must not be read"); },
  }];
  await input.listeners.change.call(input);
  assert.equal(timers.size, 0, "new import must cancel previous playback");
  assert.equal(get("play").disabled, true);
  assert.equal(get("seek").disabled, true);
  assert.equal(get("counter").textContent, "Нет записи");
  assert.match(get("status").textContent, /24 МиБ/);

  await load(trace);
  check(0, "1 / 3");
  assert.equal(get("play").disabled, false);
  assert.equal(timers.size, 0, "fresh import must remain paused");
  process.stdout.write(JSON.stringify({
    verifiedFrames: 3, navigation: "PASS", playback: "PASS",
    speedAndVisibility: "PASS", invalidImportReset: "PASS",
  }));
})().catch(error => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
"""


class OfflineViewerNavigationQA(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node stdlib VM unavailable")
    def test_verified_export_navigation_play_pause_and_import_reset(self):
        with tempfile.TemporaryDirectory(prefix="mojum-viewer-controls-") as tmp:
            root = Path(tmp)
            db = root / "synthetic.sqlite3"
            output = root / "synthetic.private.jsonl"
            with Aquarium(db, SEED, GRAPH, motor_mode="developmental_fixture",
                          sensory_mode="toy_probe") as aquarium:
                for _ in range(3):
                    aquarium.step()
            export_private_trace(
                db, output, SEED, GRAPH, allow_private_export=True,
                motor_mode="developmental_fixture", sensory_mode="toy_probe",
            )
            run = subprocess.run(
                [shutil.which("node"), "-e", NODE, str(VIEWER), str(output)],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), {
                "verifiedFrames": 3, "navigation": "PASS", "playback": "PASS",
                "speedAndVisibility": "PASS", "invalidImportReset": "PASS",
            })


if __name__ == "__main__":
    unittest.main()
