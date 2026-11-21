"""Regression for the real offline viewer's local playback controls.

Issue #319: old PR #74's import/security cases are already covered by
test_private_trace_integrity.py. This focuses on a separate gap: stepping,
seek, play/pause, speed changes, visibility pause and failed-import reset.
Exercises the CURRENT shipped viewer JS in an isolated Node stdlib VM.
No network, actual PRIVATE journal, server, npm package or live timer.
"""

from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "examples/aquarium_trace_viewer.html"

NODE_HARNESS = r"""
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {TextDecoder} = require("node:util");

const html = fs.readFileSync(process.argv[1], "utf8");
const source = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(source, "actual inline viewer script missing");
new vm.Script(source[1], {filename:"aquarium_trace_viewer.html"});

const registry = new Map(), documentListeners = {};
const timers = {active:null, created:0, cleared:0};
function make(id) {
  const el = {
    id, value:"1", max:"0", disabled:false, checked:false,
    textContent:"", hidden:false, files:[], children:[], listeners:{},
    classList:{toggle(){}},
    addEventListener(name, handler) {this.listeners[name] = handler;},
    replaceChildren() {this.children = [];},
    append(...items) {this.children.push(...items);},
    appendChild(item) {this.children.push(item);}
  };
  if (id === "room") {
    el.width = 960; el.height = 600;
    const draw = new Set(["fillRect","strokeRect","beginPath","moveTo",
                          "lineTo","stroke","arc","fill","fillText"]);
    el.getContext = () => new Proxy({}, {
      get(obj, prop) {return draw.has(prop) ? () => {} : obj[prop];},
      set(obj, prop, value) {obj[prop] = value; return true;}
    });
  }
  return el;
}
function get(id) {
  if (!registry.has(id)) registry.set(id, make(id));
  return registry.get(id);
}
const document = {
  hidden:false,
  getElementById:get,
  createElement() {return make("created");},
  addEventListener(name, handler) {documentListeners[name] = handler;}
};
vm.runInNewContext(source[1], {
  document, Number, Math, JSON, Array, String, Promise, Set, TextDecoder,
  clearInterval(handle) {
    if (timers.active === handle) timers.active = null;
    timers.cleared++;
  },
  setInterval(fn, delay) {
    assert.equal(timers.active, null, "two active playback intervals");
    const handle = {fn, delay};
    timers.active = handle; timers.created++;
    return handle;
  },
  fetch() {throw Error("viewer attempted network fetch");},
  XMLHttpRequest() {throw Error("viewer attempted XHR");},
  WebSocket() {throw Error("viewer attempted WebSocket");}
}, {timeout:3000});

const frame = tick => ({
  schema:"mojum.aquarium.frame/1", tick,
  world:{position:[1.25,2.25],blocked:false},
  interoception:{heartbeat:{pulse:tick % 2 === 0}},
  developmental:{
    observation:{range_bucket_5cm_ewns:[0,10,5,5]},
    intent:{action:"east"}
  },
  brain:{status:"UNIMPLEMENTED"}
});
const valid = [frame(0),frame(1),frame(2),{
  schema:"mojum.aquarium.checkpoint/1", stored_events:4
}].map(JSON.stringify).join("\n") + "\n";

async function importWire(wire, name) {
  const file = get("file");
  file.files = [{
    name, size:Buffer.byteLength(wire),
    arrayBuffer:async() => Uint8Array.from(Buffer.from(wire)).buffer
  }];
  await file.listeners.change.call(file);
}
const tick = () => get("tick").textContent;
const click = id => get(id).listeners.click();
(async () => {
  get("allow-legacy").checked = true;
  await importWire(valid, "synthetic.legacy.jsonl");
  assert.equal(get("play").disabled, false);
  assert.equal(tick(), "0");
  assert.equal(get("range").textContent, "0 / 10 / 5 / 5 × 5 см");
  assert.equal(get("file").value, "");

  click("next"); assert.equal(tick(), "1");
  click("prev"); assert.equal(tick(), "0");
  click("prev"); assert.equal(tick(), "0", "cannot step before first frame");
  click("end"); assert.equal(tick(), "2");
  click("play");  // At end, playback must restart from the first frame.
  assert.equal(tick(), "0");
  assert.equal(timers.active.delay, 400);
  assert.match(get("play").textContent, /Пауза/);
  timers.active.fn(); assert.equal(tick(), "1");

  get("speed").value = "4";
  get("speed").listeners.change();
  assert.equal(timers.active.delay, 100);
  assert.equal(tick(), "1");
  assert.equal(timers.created, 2);
  assert.equal(timers.cleared, 1);

  click("play"); // Pause.
  assert.equal(timers.active, null);
  assert.match(get("play").textContent, /Воспроизвести/);

  click("play");
  get("seek").value = "2";
  get("seek").listeners.input();
  assert.equal(tick(), "2");
  assert.equal(timers.active, null, "seeking must pause playback");

  click("start"); assert.equal(tick(), "0");
  click("play");
  document.hidden = true;
  documentListeners.visibilitychange();
  assert.equal(timers.active, null, "hidden tab must stop timer");

  document.hidden = false;
  click("play");
  timers.active.fn(); assert.equal(tick(), "1");
  timers.active.fn(); assert.equal(tick(), "2");
  assert.equal(timers.active, null, "last frame must stop playback");

  click("play");
  assert.notEqual(timers.active, null);
  await importWire(JSON.stringify(frame(0)) + "\n", "missing-footer.jsonl");
  assert.equal(timers.active, null, "failed import must stop playback");
  assert.equal(get("play").disabled, true);
  assert.equal(tick(), "—");
  assert.match(get("status").textContent, /checkpoint/);
  assert.equal(get("file").value, "");
  assert.equal(timers.created, timers.cleared, "no orphan playback timer");
  process.stdout.write("PASS_VIEWER_CONTROLS\n");
})().catch(err => {
  process.stderr.write(String(err.stack || err));
  process.exit(1);
});
"""


class OfflineViewerControlsQA(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node stdlib VM unavailable")
    def test_current_viewer_playback_navigation_and_failed_import_reset(self):
        run = subprocess.run(
            [shutil.which("node"), "-e", NODE_HARNESS, str(VIEWER)],
            cwd=ROOT, text=True, capture_output=True, timeout=20, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "PASS_VIEWER_CONTROLS")


if __name__ == "__main__":
    unittest.main()
