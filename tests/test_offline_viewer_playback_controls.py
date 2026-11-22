"""Regression for the existing offline viewer's playback controls.

Runs the shipped HTML JavaScript in a Node VM against a disposable, verified,
three-frame Aquarium SQLite export. No browser network, npm, or real PRIVATE
journals are required.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "examples/aquarium_trace_viewer.html"
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))

NODE_CONTROLS = r"""
"use strict";
const fs = require("node:fs"), vm = require("node:vm");
const assert = require("node:assert/strict");
const {TextDecoder, TextEncoder} = require("node:util");
const html = fs.readFileSync(process.argv[1], "utf8");
const wire = fs.readFileSync(process.argv[2], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert(script, "missing actual viewer script");

function loadViewer() {
 const dom = new Map(), handlers = {};
 const timer = {callback:null, ms:null, stops:0};
 const make = id => ({
   id, disabled:false, hidden:false, checked:false,
   value:"0", max:"0", textContent:"", children:[], listeners:{}, files:[],
   classList:{toggle(){}},
   addEventListener(type,fn){this.listeners[type]=fn;},
   replaceChildren(){this.children=[];},
   append(...items){this.children.push(...items);},
   appendChild(item){this.children.push(item);}
 });
 const get = id => {
   if(!dom.has(id)) {
     const el = make(id);
     if(id==="room") {
       el.width=960;el.height=600;
       el.getContext=()=>new Proxy({},{
         get(target,key){return ["fillRect","strokeRect","beginPath","moveTo",
           "lineTo","stroke","arc","fill","fillText"].includes(key)
           ? ()=>{} : target[key];},
         set(target,key,value){target[key]=value;return true;}
       });
     }
     dom.set(id,el);
   }
   return dom.get(id);
 };
 const document = {
   getElementById:get,createElement:()=>make("created"),
   addEventListener(type,fn){handlers[type]=fn;},hidden:false
 };
 vm.runInNewContext(script[1],{
   document,Number,Math,JSON,Array,String,Promise,Set,
   crypto:require("node:crypto").webcrypto,TextEncoder,TextDecoder,
   setInterval(fn,ms) {
     assert.equal(timer.callback,null,"duplicate playback timer");
     timer.callback=fn;timer.ms=ms;return 1;
   },
   clearInterval(id) {
     assert.equal(id,1);timer.callback=null;timer.ms=null;timer.stops++;
   },
   fetch(){throw Error("forbidden network fetch");},
   WebSocket(){throw Error("forbidden WebSocket");}
 },{timeout:3000});
 return {get,document,handlers,timer};
}

(async()=>{
 const {get,document,handlers,timer}=loadViewer(), input=get("file");
 const bytes=Buffer.from(wire,"utf8");
 input.files=[{name:"synthetic.private.jsonl",size:bytes.length,
   arrayBuffer:async()=>Uint8Array.from(bytes).buffer}];
 await input.listeners.change.call(input);
 assert.equal(get("play").disabled,false,"verified file rejected");
 assert.equal(get("tick").textContent,"0");
 assert.equal(get("seek").max,"2");
 assert.match(get("status").textContent,/SHA-256/);
 assert.equal(input.value,"");

 // Play, pause, resume, end-of-trace and restarting from EOF.
 get("speed").value="1";
 get("play").listeners.click();
 assert.equal(timer.ms,400);
 assert.match(get("play").textContent,/Пауза/);
 get("play").listeners.click();
 assert.equal(timer.callback,null);
 assert.match(get("play").textContent,/Воспроизвести/);
 get("play").listeners.click();
 timer.callback();
 assert.equal(get("tick").textContent,"1");
 timer.callback();
 assert.equal(get("tick").textContent,"2");
 assert.equal(timer.callback,null,"playback did not stop at EOF");
 get("play").listeners.click();
 assert.equal(get("tick").textContent,"0");

 // Changing speed swaps one timer, manual seek pauses, and buttons navigate.
 get("speed").value="2";
 get("speed").listeners.change();
 assert.equal(timer.ms,200);
 get("seek").value="1";
 get("seek").listeners.input();
 assert.equal(timer.callback,null);
 assert.equal(get("tick").textContent,"1");
 get("prev").listeners.click();
 assert.equal(get("tick").textContent,"0");
 get("next").listeners.click();
 assert.equal(get("tick").textContent,"1");
 get("end").listeners.click();
 assert.equal(get("tick").textContent,"2");
 get("start").listeners.click();
 assert.equal(get("tick").textContent,"0");

 // Backgrounding and replacing the chosen file must cancel live playback.
 get("play").listeners.click();
 assert.equal(timer.ms,200);
 document.hidden=true;
 handlers.visibilitychange();
 assert.equal(timer.callback,null);
 document.hidden=false;
 get("play").listeners.click();
 await input.listeners.change.call(input);
 assert.equal(timer.callback,null);
 assert.equal(get("tick").textContent,"0");
 assert.equal(get("play").disabled,false);
 assert.equal(get("seek").max,"2");
 assert.ok(timer.stops>=5,"timers did not stop for every state change");

 // Oversized replacement during active playback must cancel the timer before
 // any file read, reject the new trace, and permit a clean later reload.
 get("play").listeners.click();
 assert.equal(timer.ms,200);
 let oversizeRead=false;
 input.files=[{name:"oversized.private.jsonl",size:24*1024*1024+1,
   arrayBuffer:async()=>{oversizeRead=true;throw Error("oversize file was read");}}];
 await input.listeners.change.call(input);
 assert.equal(oversizeRead,false,"oversized import read bytes before rejection");
 assert.equal(timer.callback,null,"oversized import left playback timer running");
 assert.equal(get("play").disabled,true,"old playback remained enabled");
 assert.equal(get("seek").disabled,true,"seek remained enabled on invalid import");
 assert.equal(get("seek").max,"0","seek retained stale range after invalid import");
 assert.equal(get("tick").textContent,"—","stale frame survived rejected import");
 assert.match(get("status").textContent,/24 МиБ/);
 input.files=[{name:"synthetic.private.jsonl",size:bytes.length,
   arrayBuffer:async()=>Uint8Array.from(bytes).buffer}];
 await input.listeners.change.call(input);
 assert.equal(get("play").disabled,false,"valid reload did not recover");
 assert.equal(get("tick").textContent,"0");
 assert.equal(timer.callback,null,"old timer survived valid recovery");
 
 // Small malformed JSONL forces a parse failure *after* arrayBuffer(), not
 // the earlier size rejection. Neither the old frame nor its timer may survive.
 get("play").listeners.click();
 assert.equal(timer.ms,200);
 const malformed=Buffer.from("{broken jsonl\\n","utf8");
 let malformedReads=0;
 input.files=[{name:"malformed.private.jsonl",size:malformed.length,
   arrayBuffer:async()=>{malformedReads++;return Uint8Array.from(malformed).buffer;}}];
 await input.listeners.change.call(input);
 assert.equal(malformedReads,1,"malformed replacement was not parsed");
 assert.equal(timer.callback,null,"malformed replacement left playback running");
 assert.equal(get("play").disabled,true,"old playback enabled after parse error");
 assert.equal(get("seek").disabled,true,"old seek enabled after parse error");
 assert.equal(get("seek").max,"0","malformed trace retained stale seek range");
 assert.equal(get("tick").textContent,"—","malformed trace retained stale frame");
 input.files=[{name:"synthetic.private.jsonl",size:bytes.length,
   arrayBuffer:async()=>Uint8Array.from(bytes).buffer}];
 await input.listeners.change.call(input);
 assert.equal(get("play").disabled,false,"valid reload after malformed file rejected");
 assert.equal(get("seek").disabled,false,"seek not restored after valid reload");
 assert.equal(get("tick").textContent,"0");
 assert.equal(timer.callback,null,"stale timer survived malformed-file recovery");
 process.stdout.write(JSON.stringify({ok:true,frames:3,stops:timer.stops})+"\n");
})().catch(err=>{process.stderr.write(String(err.stack||err));process.exit(1)});
"""


class OfflineViewerPlaybackQA(unittest.TestCase):
    def test_missing_node_fails_ci_but_skips_optional_local_run(self):
        with mock.patch("shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": ""}):
                with self.assertRaisesRegex(AssertionError, "Node is required in CI"):
                    self.test_play_pause_speed_seek_visibility_and_file_replace()
            with mock.patch.dict(os.environ, {"CI": "", "GITHUB_ACTIONS": ""}):
                with self.assertRaises(unittest.SkipTest):
                    self.test_play_pause_speed_seek_visibility_and_file_replace()

    def test_play_pause_speed_seek_visibility_and_file_replace(self):
        node = shutil.which("node")
        if node is None:
            if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
                self.fail("Node is required in CI for viewer playback acceptance")
            self.skipTest("Node unavailable for optional local viewer test")
        with tempfile.TemporaryDirectory(prefix="mojum-viewer-controls-") as tmp:
            source = Path(tmp) / "synthetic.sqlite3"
            output = Path(tmp) / "synthetic.private.jsonl"
            with Aquarium(source, SEED, GRAPH,
                          motor_mode="developmental_fixture",
                          sensory_mode="toy_probe") as aquarium:
                for _ in range(3):
                    aquarium.step()
            export_private_trace(
                source, output, SEED, GRAPH, allow_private_export=True,
                motor_mode="developmental_fixture", sensory_mode="toy_probe",
            )
            run = subprocess.run(
                [node, "-e", NODE_CONTROLS,
                 str(VIEWER), str(output)],
                cwd=ROOT, env=dict(os.environ), encoding="utf-8",
                capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(run.stdout)
            self.assertTrue(report["ok"])
            self.assertEqual(report["frames"], 3)
            self.assertGreaterEqual(report["stops"], 5)


if __name__ == "__main__":
    unittest.main()
