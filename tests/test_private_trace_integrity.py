"""PRIVATE JSONL SHA-256 byte-integrity is checked by the real viewer JS.

Source frames originate in actual R0.13 Aquarium + SQLite, not a fabricated
web animation. Test only synthetic local files; never attach to CI artifacts.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium
from mojum.aquarium_trace_export import export_private_trace


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "src/mojum/data/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "src/mojum/data/first_loop.json").read_text(encoding="utf-8"))
VIEWER = ROOT / "examples/aquarium_trace_viewer.html"


NODE_HARNESS = r"""
"use strict";
const fs=require("node:fs"),vm=require("node:vm");
const html=fs.readFileSync(process.argv[1],"utf8");
const wire=fs.readFileSync(process.argv[2],"utf8");
const script=html.match(/<script>([\s\S]*?)<\/script>/);
if(!script)throw Error("missing viewer script");
const {TextDecoder,TextEncoder}=require("node:util");
function openDOM(){
 const registry=new Map();
 const make=id=>({
   id,disabled:false,checked:false,value:"0",max:"0",textContent:"",
   children:[],listeners:{},files:[],classList:{toggle(){}},
   addEventListener(event,fn){this.listeners[event]=fn;},
   replaceChildren(){this.children=[];},
   append(...items){this.children.push(...items);},
   appendChild(item){this.children.push(item);}
 });
 const get=id=>{
   if(!registry.has(id)){
      const e=make(id);
      if(id==="room"){
        e.width=960;e.height=600;
        e.getContext=()=>new Proxy({},{
          get(obj,p){return ["fillRect","strokeRect","beginPath","moveTo",
            "lineTo","stroke","arc","fill","fillText"].includes(p)?()=>{}:obj[p];},
          set(obj,p,v){obj[p]=v;return true}
        });
      }
      registry.set(id,e);
   }
   return registry.get(id);
 };
 const document={
   getElementById:get,
   createElement:()=>make("created"),
   addEventListener(){},
   hidden:false
 };
 vm.runInNewContext(script[1],{
    document,Number,Math,JSON,Array,String,Promise,Set,
    crypto:require("node:crypto").webcrypto,TextEncoder,TextDecoder,
    clearInterval(){},setInterval(){throw Error("unexpected timer")},
    fetch(){throw Error("forbidden fetch")},
    WebSocket(){throw Error("forbidden websocket")}
 },{timeout:3000});
 return get;
}
async function attempt(source, allowLegacy=false){
 const get=openDOM(),input=get("file");
 get("allow-legacy").checked=allowLegacy;
 input.files=[{name:"recorded.private.jsonl",
   size:Buffer.byteLength(source),arrayBuffer:async()=>Uint8Array.from(Buffer.from(source)).buffer}];
 await input.listeners.change.call(input);
 return {
   enabled:!get("play").disabled,
   status:get("status").textContent,
   tick:get("tick").textContent,
   probe:get("probe").textContent,
   pred:get("prediction").textContent
 };
}
function replaceFooter(source,transform){
 const lines=source.trimEnd().split("\n");
 const last=JSON.parse(lines[lines.length-1]);
 transform(last);
 lines[lines.length-1]=JSON.stringify(last);
 return lines.join("\n")+"\n";
}
(async()=>{
 const valid=await attempt(wire);
 const altered=await attempt(wire.replace('"measured_deflection":0.75',
                                         '"measured_deflection":0.15'));
 const truncated=await attempt(wire.trimEnd().split("\n").slice(0,-1).join("\n")+"\n");
 const mismatch=await attempt(replaceFooter(wire,
   f=>{f.trace_range.next_tick+=1}));
 const brokenProof=await attempt(replaceFooter(wire,
   f=>{f.trace_integrity.sha256="0".repeat(64)}));
 const missingProof=await attempt(replaceFooter(wire, f=>{delete f.trace_integrity}));
 const strippedModern=await attempt(replaceFooter(wire,
   f=>{delete f.trace_integrity;delete f.export_status}), true);
 const strippedRange=await attempt(replaceFooter(wire,
   f=>{delete f.trace_integrity;delete f.export_status;delete f.trace_range}), true);
 const missingStatus=await attempt(replaceFooter(wire,
   f=>{delete f.export_status}), true);
 const missingRange=await attempt(replaceFooter(wire,
   f=>{delete f.trace_range}), true);
 // A present-but-forged status is not "legacy": it must fail closed even
 // when the operator explicitly opts into unverified historical traces.
 const wrongStatus=await attempt(replaceFooter(wire,
   f=>{f.export_status="LEGACY_UNVERIFIED"}, true));
 const frameCountMismatch=await attempt(replaceFooter(wire,
   f=>{f.trace_integrity.frame_count+=1}));
 const byteLengthMismatch=await attempt(replaceFooter(wire,
   f=>{f.trace_integrity.byte_length+=1}));
 // Frames after the single terminal checkpoint would otherwise extend the
 // replay past the byte range the proof actually covers.
 const afterCheckpoint=await attempt(wire.trimEnd()+"\n"+
   JSON.stringify({schema:"mojum.aquarium.frame/1",tick:99,
     world:{position:[0,0],blocked:false},
     interoception:{heartbeat:{pulse:false}}})+"\n", true);
 const legacy=replaceFooter(wire,
   f=>{delete f.trace_integrity;delete f.export_status;delete f.trace_range;
       delete f.trace_room});
 const legacyDefault=await attempt(legacy);
 const legacyOptIn=await attempt(legacy, true);
 const blankLine=await attempt(wire.replace("\n", "\n\n"));
 const whitespaceLine=await attempt(wire.replace("\n", "\n \n"));
 const trailingBlank=await attempt(wire + "\n");
 const encoding=await attempt(Buffer.concat([Buffer.from(wire),Buffer.from([255])]));
 process.stdout.write(JSON.stringify({valid,altered,truncated,mismatch,
                                      brokenProof,missingProof,strippedModern,
                                      strippedRange,missingStatus,missingRange,
                                      wrongStatus,frameCountMismatch,
                                      byteLengthMismatch,afterCheckpoint,
                                      legacyDefault,legacyOptIn,
                                      blankLine,whitespaceLine,trailingBlank,
                                      encoding})+"\n");
})().catch(err=>{process.stderr.write(String(err.stack||err));process.exit(1)});
"""


class PrivateTraceIntegrityQA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "synthetic.sqlite3"
        self.output = Path(tmp.name) / "private.jsonl"
        with Aquarium(self.db, SEED, GRAPH,
                      motor_mode="developmental_fixture",
                      sensory_mode="toy_probe") as q:
            self.recorded = [q.step() for _ in range(3)]
        self.report = export_private_trace(
            self.db, self.output, SEED, GRAPH,
            allow_private_export=True, motor_mode="developmental_fixture",
            sensory_mode="toy_probe",
        )

    def test_producer_digest_matches_exact_exported_frame_bytes(self):
        wire = self.output.read_bytes()
        lines = wire.splitlines(keepends=True)
        self.assertTrue(all(line.endswith(b"\n") for line in lines))
        footer = json.loads(lines[-1])
        proof = footer["trace_integrity"]
        payload = b"".join(lines[:-1])
        self.assertEqual(proof["schema"], "mojum.aquarium.trace-frames-sha256/1")
        self.assertEqual(proof["byte_length"], len(payload))
        self.assertEqual(proof["frame_count"], len(self.recorded))
        self.assertEqual(proof["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(self.report["trace_sha256"], hashlib.sha256(wire).hexdigest())
        self.assertEqual(
            [json.loads(line) for line in lines[:-1]], self.recorded
        )

    def test_slice_digest_covers_only_exported_but_replayed_frames(self):
        later = self.output.with_name("partial.jsonl")
        report = export_private_trace(
            self.db, later, SEED, GRAPH, allow_private_export=True,
            motor_mode="developmental_fixture", sensory_mode="toy_probe",
            start_tick=1, frame_limit=2,
        )
        lines = later.read_bytes().splitlines(keepends=True)
        footer = json.loads(lines[-1])
        self.assertEqual([json.loads(x) for x in lines[:-1]], self.recorded[1:])
        self.assertEqual(footer["trace_range"]["start_tick"], 1)
        self.assertEqual(footer["trace_integrity"]["frame_count"], 2)
        self.assertEqual(footer["trace_integrity"]["sha256"],
                         hashlib.sha256(b"".join(lines[:-1])).hexdigest())
        self.assertFalse(report["has_more"])

    @unittest.skipUnless(shutil.which("node"), "Node unavailable; browser CI mandatory")
    def test_actual_viewer_js_verifies_hash_and_rejects_corrupt_truncated_files(self):
        env = dict(os.environ)
        run = subprocess.run(
            [shutil.which("node"), "-e", NODE_HARNESS,
             str(VIEWER), str(self.output)],
            cwd=ROOT, env=env, encoding="utf-8", capture_output=True,
            check=False, timeout=30,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        values = json.loads(run.stdout)
        self.assertTrue(values["valid"]["enabled"])
        self.assertEqual(values["valid"]["tick"], "0")
        self.assertIn("SHA-256 кадров совпадает", values["valid"]["status"])
        self.assertEqual(values["valid"]["probe"], "Запрошена")
        self.assertFalse(values["altered"]["enabled"])
        self.assertIn("SHA-256", values["altered"]["status"])
        self.assertFalse(values["truncated"]["enabled"])
        self.assertIn("checkpoint", values["truncated"]["status"])
        self.assertFalse(values["mismatch"]["enabled"])
        self.assertIn("диапазоном", values["mismatch"]["status"])
        self.assertFalse(values["brokenProof"]["enabled"])
        self.assertIn("SHA-256", values["brokenProof"]["status"])
        self.assertFalse(values["missingProof"]["enabled"])
        self.assertIn("SHA-256", values["missingProof"]["status"])
        for name in ("missingStatus", "missingRange"):
            self.assertFalse(values[name]["enabled"], name)
            self.assertIn("SHA-256", values[name]["status"], name)
        for name in ("blankLine", "whitespaceLine", "trailingBlank"):
            self.assertFalse(values[name]["enabled"], name)
            self.assertIn("Пустая JSONL-строка", values[name]["status"], name)
        self.assertFalse(values["encoding"]["enabled"])
        self.assertFalse(values["strippedModern"]["enabled"])
        self.assertIn("SHA-256", values["strippedModern"]["status"])
        self.assertFalse(values["strippedRange"]["enabled"])
        self.assertIn("SHA-256", values["strippedRange"]["status"])
        for name in ("missingStatus", "missingRange"):
            self.assertFalse(values[name]["enabled"], name)
            self.assertIn("SHA-256", values[name]["status"], name)
        # A forged status value must not impersonate the explicit legacy path.
        self.assertFalse(values["wrongStatus"]["enabled"])
        self.assertIn("SHA-256", values["wrongStatus"]["status"])
        # The proof binds both the frame count and the exact byte length.
        self.assertFalse(values["frameCountMismatch"]["enabled"])
        self.assertIn("метаданные SHA-256", values["frameCountMismatch"]["status"])
        self.assertFalse(values["byteLengthMismatch"]["enabled"])
        self.assertIn("Размер записанных кадров",
                      values["byteLengthMismatch"]["status"])
        # Nothing may follow the single terminal checkpoint, even with the
        # legacy opt-in enabled.
        self.assertFalse(values["afterCheckpoint"]["enabled"])
        self.assertIn("checkpoint", values["afterCheckpoint"]["status"])
        self.assertFalse(values["legacyDefault"]["enabled"])
        self.assertIn("legacy", values["legacyDefault"]["status"])
        self.assertTrue(values["legacyOptIn"]["enabled"])
        self.assertIn("не проверен", values["legacyOptIn"]["status"])


if __name__ == "__main__":
    unittest.main()
