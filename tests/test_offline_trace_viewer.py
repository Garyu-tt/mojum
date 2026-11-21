"""Offline trace viewer contract and functional no-network DOM smoke.

The Node stdlib VM uses a small fake DOM so the file:// viewer JS is exercised
without Playwright, a dev server, network, browser automation or test secrets.
The UI only loads user-supplied synthetic PRIVATE trace fixtures in memory.
"""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "examples" / "aquarium_trace_viewer.html"


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.external_scripts = []
        self.csp = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "script" and "src" in attributes:
            self.external_scripts.append(attributes["src"])
        if tag == "meta" and attributes.get("http-equiv") == "Content-Security-Policy":
            self.csp = attributes.get("content", "")


NODE_HARNESS = r"""
"use strict";
const fs=require("node:fs");
const vm=require("node:vm");
const raw=fs.readFileSync(process.argv[1],"utf8");
const match=raw.match(/<script>([\s\S]*?)<\/script>/);
if(!match)throw Error("inline viewer script missing");
new vm.Script(match[1],{filename:"aquarium_trace_viewer.html"});
function fakeDOM(){
  const els = new Map();
  function make(id){
    const e={
      id, disabled:false, value:"0", max:"0", textContent:"",
      children:[], listeners:{}, files:[],
      classList:{toggle(){}},
      addEventListener(name,handler){this.listeners[name]=handler;},
      replaceChildren(){this.children=[];},
      appendChild(child){this.children.push(child);},
      append(...children){this.children.push(...children);},
    };
    if(id==="room"){
      e.width=960;e.height=600;
      e.getContext=()=>new Proxy({},{
        get(target, prop){
          if(["fillRect","strokeRect","beginPath","moveTo","lineTo","stroke",
              "arc","fill","fillText"].includes(prop)) return ()=>{};
          return target[prop];
        },
        set(target,prop,value){target[prop]=value;return true;}
      });
    }
    return e;
  }
  const get=id=>{
    if(!els.has(id))els.set(id,make(id));
    return els.get(id);
  };
  const document={
    getElementById:get,
    createElement:()=>make("created"),
    addEventListener(){},
    hidden:false
  };
  const globals={
    document,Number,Math,JSON,Array,Promise,Error,String,Set,clearInterval(){},
    setInterval(){throw Error("unexpected timer in import smoke")},
    fetch(){throw Error("forbidden network request")},
    XMLHttpRequest(){throw Error("forbidden XHR")},
    WebSocket(){throw Error("forbidden socket")}
  };
  vm.runInNewContext(match[1],globals,{timeout:1000});
  return get;
}
async function upload(get,wire,large=false){
  const input=get("file");
  input.files=[{
    name:"dante.private.jsonl",
    size:large?25*1024*1024:wire.length,
    text:async()=>wire
  }];
  await input.listeners.change.call(input);
}
const frame=(tick,blocked)=>({
  schema:"mojum.aquarium.frame/1",
  tick,
  world:{position:[1.20,2.10],blocked},
  interoception:{heartbeat:{pulse:true,phase_milli:100}},
  developmental:{
    observation:{range_bucket_5cm_ewns:[0,12,5,6]},
    intent:{action:"x-"},
    learning:{total_updates:tick+1}
  },
  brain:{status:"UNIMPLEMENTED"},
  toy_contact:{
    observation:{visible:true,reflectance_rgb:[120,80,70],range_bucket_10cm:1},
    probe_intent:{requested:true},
    measured_contact:{contact:true,measured_deflection:0.75},
    prediction_before:{predicted_deflection:0.5,model_updates:2}
  }
});
(async()=>{
  const get=fakeDOM();
  const first=frame(4,false), second=frame(5,true);
  second.brain.status="<img src=x onerror=alert(1)>";
  const valid=[first,second,{
    schema:"mojum.aquarium.checkpoint/1",
    stored_events:7
  }].map(JSON.stringify).join("\n");
  await upload(get,valid);
  const firstState={
    counter:get("counter").textContent,
    tick:get("tick").textContent,
    ray:get("range").textContent,
    probe:get("probe").textContent,
    deflection:get("deflection").textContent,
    prediction:get("prediction").textContent,
    updates:get("learned").textContent,
    disabled:get("play").disabled
  };
  get("seek").value="1";
  get("seek").listeners.input();
  const secondState={
    counter:get("counter").textContent,
    blocked:get("blocked").textContent,
    brain:get("brain").textContent,
    injectedElements:get("events").children.length
  };
  const bad=fakeDOM();
  const discontinuous=[frame(4,false),frame(6,false)]
       .map(JSON.stringify).join("\n");
  await upload(bad,discontinuous);
  const error={
    disabled:bad("play").disabled,
    status:bad("status").textContent
  };
  const oversized=fakeDOM();
  await upload(oversized,"NOT_JSON",true);
  const large={
    disabled:oversized("play").disabled,
    status:oversized("status").textContent
  };
  const racing=fakeDOM();
  const racingInput=racing("file");
  let resolveSlow;
  racingInput.files=[{size:1024,name:"old.jsonl",
     text:()=>new Promise(resolve=>{resolveSlow=resolve;})}];
  const olderPromise=racingInput.listeners.change.call(racingInput);
  racingInput.files=[{size:1024,name:"current.jsonl",
     text:async()=>valid}];
  await racingInput.listeners.change.call(racingInput);
  resolveSlow(JSON.stringify(frame(100,false)));
  await olderPromise;
  const latest={
    tick:racing("tick").textContent,
    status:racing("status").textContent,
    reusableInput:racingInput.value
  };
  process.stdout.write(JSON.stringify({firstState,secondState,error,large,latest})+"\n");
})().catch(err=>{process.stderr.write(String(err.stack||err));process.exit(1);});
"""


class OfflineTraceViewerTests(unittest.TestCase):
    def test_markup_has_single_screen_controls_and_denies_network(self):
        html = PAGE.read_text(encoding="utf-8")
        parsed = Markup()
        parsed.feed(html)
        for required in ("file", "room", "seek", "play", "prev", "next",
                         "tick", "position", "pulse", "action", "blocked",
                         "range", "probe", "deflection", "prediction",
                         "learned", "brain", "events", "status"):
            self.assertIn(required, parsed.ids)
        self.assertEqual(parsed.external_scripts, [])
        self.assertIn("connect-src 'none'", parsed.csp)
        self.assertIn("default-src 'none'", parsed.csp)
        self.assertIn("img-src 'none'", parsed.csp)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)

    @unittest.skipUnless(shutil.which("node"), "Node stdlib VM unavailable")
    def test_local_jsonl_import_telemetry_scrub_and_invalid_file_rejection(self):
        completed = subprocess.run(
            [shutil.which("node"), "-e", NODE_HARNESS, str(PAGE)],
            cwd=ROOT, text=True, capture_output=True, timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        first = result["firstState"]
        self.assertEqual(first["counter"], "1 / 2")
        self.assertEqual(first["tick"], "4")
        self.assertEqual(first["ray"], "0 / 12 / 5 / 6 × 5 см")
        self.assertEqual(first["probe"], "Запрошена")
        self.assertEqual(first["deflection"], "0.75")
        self.assertEqual(first["prediction"], "0.5")
        self.assertEqual(first["updates"], "2")
        self.assertFalse(first["disabled"])
        next_ = result["secondState"]
        self.assertEqual(next_["counter"], "2 / 2")
        self.assertEqual(next_["blocked"], "Заблокировано")
        self.assertEqual(next_["brain"], "<img src=x onerror=alert(1)>")
        self.assertGreater(next_["injectedElements"], 0)
        rejected = result["error"]
        self.assertTrue(rejected["disabled"])
        self.assertIn("Пропуск", rejected["status"])
        oversized = result["large"]
        self.assertTrue(oversized["disabled"])
        self.assertIn("24 МБ", oversized["status"])
        newest = result["latest"]
        self.assertEqual(newest["tick"], "4")
        self.assertIn("current.jsonl", newest["status"])
        self.assertEqual(newest["reusableInput"], "")


if __name__ == "__main__":
    unittest.main()
