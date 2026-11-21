"""R0.13 integration: real learned toy contact -> SQLite -> offline observer.

Not a browser-generated creature. Tests check full history replay, explicit
privacy consent, no-oracle numeric observations, negative mode controls and
the real HTML's JavaScript displaying the exported experience.
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

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_trace_export import export_private_trace
from mojum.noosphere import NoosphereError
from mojum.sensory_play import Toy, ToyScene


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
VIEWER = ROOT / "examples" / "aquarium_trace_viewer.html"


class IntegratedR013RealTraceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "dante.sqlite3"
        self.trace = self.root / "dante.private.jsonl"

    def baby(self, **options):
        return Aquarium(self.db, SEED, GRAPH,
                        exploration_mode="goal_free", sensory_mode="toy_probe",
                        **options)

    def private_export(self, **options):
        return export_private_trace(
            self.db, self.trace, SEED, GRAPH,
            allow_private_export=True,
            exploration_mode="goal_free", sensory_mode="toy_probe",
            **options,
        )

    def born(self, ticks=12):
        with self.baby() as a:
            frames = [a.step() for _ in range(ticks)]
            head = a.checkpoint()["journal_head"]
            count = a.store.count()
            recorded = a.sensory_bridge.model.snapshot()
        return frames, head, count, recorded

    def test_actual_contact_perception_motor_and_learning_survive_verified_export(self):
        observed, head, count, model = self.born(12)
        source_before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        info = self.private_export(expected_journal_head=head)
        self.assertEqual(info["frames"], 12)
        self.assertEqual(info["journal_head"], head)
        self.assertEqual(info["event_count"], count)
        self.assertEqual(info["source_total_ticks"], 12)
        self.assertEqual(info["first_tick"], 0)
        self.assertEqual(info["next_tick"], 12)
        self.assertFalse(info["has_more"])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),
                         source_before)
        lines = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(lines[:-1], observed)
        self.assertEqual(lines[-1]["stored_events"], count)
        self.assertEqual(lines[-1]["journal_head"], head)
        self.assertNotIn("neural_state", lines[-1])
        first = lines[0]
        second = lines[1]
        self.assertEqual(first["brain"]["status"], "UNIMPLEMENTED")
        self.assertIsNone(first["brain"]["action"])
        self.assertTrue(first["toy_contact"]["probe_intent"]["requested"])
        self.assertTrue(first["toy_contact"]["measured_contact"]["contact"])
        self.assertEqual(first["toy_contact"]["measured_contact"]["measured_deflection"], .75)
        self.assertEqual(first["toy_contact"]["learning"]["new_prediction"], .75)
        self.assertEqual(second["toy_contact"]["prediction_before"]["predicted_deflection"], .75)
        self.assertGreaterEqual(model["updates"], 1)
        self.assertEqual(first["developmental"]["learning"]["total_updates"], 1)
        cue = first["toy_contact"]["observation"]
        self.assertEqual(set(cue), {"schema", "tick", "world_revision", "visible",
                                   "reflectance_rgb", "range_bucket_10cm", "source"})
        self.assertNotIn("softness", cue)
        self.assertNotIn("id", cue)
        self.assertNotIn("toys", cue)
        self.assertTrue(info["classification"].startswith("PRIVATE"))

    def test_replay_is_fail_closed_on_wrong_modes_and_head(self):
        _, trusted_head, _, _ = self.born(3)
        with self.assertRaises(AquariumError):
            export_private_trace(
                self.db, self.trace, SEED, GRAPH,
                allow_private_export=True,
                exploration_mode="scripted", sensory_mode="none",
            )
        self.assertFalse(self.trace.exists())
        with self.assertRaises(AquariumError):
            export_private_trace(
                self.db, self.trace, SEED, GRAPH,
                allow_private_export=True,
                exploration_mode="goal_free", sensory_mode="none",
            )
        self.assertFalse(self.trace.exists())
        with self.assertRaises(NoosphereError):
            self.private_export(expected_journal_head="a" * 64)
        self.assertFalse(self.trace.exists())
        valid = self.private_export(expected_journal_head=trusted_head)
        self.assertEqual(valid["journal_head"], trusted_head)

    def test_custom_hidden_scene_must_match_exact_private_bootstrap(self):
        custom = ToyScene(toys=(
            Toy("private.a", 1.20, 2.0, .15, (80, 100, 120), .27),
        ))
        with self.baby(sensory_scene=custom) as a:
            a.step()
            head = a.store.head()
        with self.assertRaises(AquariumError):
            self.private_export(expected_journal_head=head)
        self.assertFalse(self.trace.exists())
        accepted = self.private_export(expected_journal_head=head,
                                       sensory_scene=custom)
        self.assertEqual(accepted["frames"], 1)
        record = json.loads(self.trace.read_text().splitlines()[0])
        self.assertEqual(record["toy_contact"]["observation"]["reflectance_rgb"],
                         [80, 100, 120])
        self.assertNotIn("id", record["toy_contact"]["observation"])

    def test_actual_contact_trace_slice_keeps_history_and_replay(self):
        observed, head, _, _ = self.born(22)
        result = self.private_export(expected_journal_head=head,
                                     start_tick=8, frame_limit=7)
        records = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(records[:-1], observed[8:15])
        self.assertEqual(result["frames"], 7)
        self.assertEqual(result["first_tick"], 8)
        self.assertEqual(result["next_tick"], 15)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["source_total_ticks"], 22)
        self.assertEqual(records[-1]["trace_range"]["start_tick"], 8)
        self.assertEqual(records[-1]["trace_range"]["next_tick"], 15)
        self.assertEqual(records[-1]["journal_head"], head)

    def test_live_wal_export_does_not_modify_running_organism(self):
        with self.baby() as a:
            first = a.step()
            bytes_before = self.db.read_bytes()
            recorded = a.checkpoint()
            result = self.private_export(
                expected_journal_head=recorded["journal_head"])
            self.assertEqual(self.db.read_bytes(), bytes_before)
            self.assertEqual(a.checkpoint(), recorded)
            a.step()
            self.assertNotEqual(a.checkpoint()["journal_head"],
                                recorded["journal_head"])
        lines = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(lines[0], first)
        self.assertEqual(lines[-1]["journal_head"], result["journal_head"])
        self.assertEqual(result["frames"], 1)

    def test_r013_cli_generates_real_exportable_private_trace(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        birth = [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.db), "--ticks", "4",
            "--exploration-mode", "goal_free",
            "--sensory-mode", "toy_probe",
        ]
        for _ in range(2):
            run = subprocess.run(birth, cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
        export = subprocess.run([
            sys.executable, "-m", "mojum.aquarium_trace_export",
            "--db", str(self.db), "--output", str(self.trace),
            "--exploration-mode", "goal_free",
            "--sensory-mode", "toy_probe",
            "--allow-private-export",
            "--start-tick", "2", "--frame-limit", "3",
        ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(export.returncode, 0, export.stderr)
        metadata = json.loads(export.stdout)
        self.assertEqual(metadata["frames"], 3)
        self.assertEqual(metadata["next_tick"], 5)
        self.assertEqual(metadata["source_total_ticks"], 8)
        self.assertNotIn("measured_deflection", export.stdout)
        records = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual([x["tick"] for x in records[:-1]], [2, 3, 4])
        self.assertIn("toy_contact", records[0])
        self.assertFalse(records[0]["brain"]["status"] != "UNIMPLEMENTED")


NODE_ACTUAL_TRACE = r"""
const fs=require("node:fs"),vm=require("node:vm");
const html=fs.readFileSync(process.argv[1],"utf8");
const raw=fs.readFileSync(process.argv[2],"utf8");
const script=html.match(/<script>([\s\S]*?)<\/script>/);
if(!script)throw Error("viewer inline script missing");
const nodes=new Map();
const get=id=>{
 if(nodes.has(id))return nodes.get(id);
 const node={id,disabled:false,value:"0",max:"0",textContent:"",
    children:[],listeners:{},files:[],
    classList:{toggle(){}},
    replaceChildren(){this.children=[]},
    append(...parts){this.children.push(...parts)},
    appendChild(child){this.children.push(child)},
    addEventListener(name,cb){this.listeners[name]=cb}};
 if(id==="room"){
   node.width=960;node.height=600;
   node.getContext=()=>new Proxy({},{
       get(t,p){return ["fillRect","strokeRect","beginPath","moveTo","lineTo",
          "stroke","arc","fill","fillText"].includes(p)?()=>{}:t[p]},
       set(t,p,v){t[p]=v;return true}});
 }
 nodes.set(id,node);return node;
};
const doc={
 getElementById:get,createElement:()=>({
   className:"",textContent:"",children:[],append(...xs){this.children.push(...xs)}
 }),addEventListener(){},hidden:false
};
const globals={document:doc,JSON,Math,Number,Promise,Array,String,
 crypto:require('node:crypto').webcrypto,
 TextEncoder:require('node:util').TextEncoder,
 setInterval(){throw Error("unexpected playback timer")},
 clearInterval(){},
 fetch(){throw Error("network forbidden")},
 WebSocket(){throw Error("network forbidden")}};
vm.runInNewContext(script[1],globals,{timeout:2000});
(async()=>{
 const input=get("file");
 input.files=[{name:"real-canonical.private.jsonl",
     size:Buffer.byteLength(raw),text:async()=>raw}];
 await input.listeners.change.call(input);
 const first={
   tick:get("tick").textContent,
   action:get("action").textContent,
   probe:get("probe").textContent,
   measured:get("deflection").textContent,
   prediction:get("prediction").textContent,
   blocked:get("blocked").textContent,
   brain:get("brain").textContent,
   status:get("status").textContent,
 };
 get("seek").value="1";get("seek").listeners.input();
 const second={tick:get("tick").textContent,
    prediction:get("prediction").textContent,
    probe:get("probe").textContent,
    frames:get("counter").textContent};
 process.stdout.write(JSON.stringify({first,second})+"\n");
})().catch(e=>{process.stderr.write(String(e.stack||e));process.exit(1)});
"""


class ActualBrowserJavaScriptFromR013SQLiteTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node VM unavailable")
    def test_real_sqlite_to_viewer_dom_renders_measured_touch_and_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "real.sqlite3"
            dest = Path(tmp) / "private.jsonl"
            with Aquarium(db, SEED, GRAPH,
                          exploration_mode="goal_free",
                          sensory_mode="toy_probe") as a:
                first = a.step()
                second = a.step()
            export_private_trace(db, dest, SEED, GRAPH,
                                 allow_private_export=True,
                                 exploration_mode="goal_free",
                                 sensory_mode="toy_probe")
            run = subprocess.run(
                [shutil.which("node"), "-e", NODE_ACTUAL_TRACE,
                 str(VIEWER), str(dest)],
                cwd=ROOT, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(run.stdout)
            one, two = result["first"], result["second"]
            self.assertEqual(one["tick"], str(first["tick"]))
            self.assertEqual(one["action"], first["developmental"]["intent"]["action"])
            self.assertEqual(one["probe"], "Запрошена")
            self.assertEqual(one["measured"], "0.75")
            self.assertEqual(one["prediction"], "—")
            self.assertEqual(one["brain"], "UNIMPLEMENTED")
            self.assertIn("real-canonical.private.jsonl", one["status"])
            self.assertEqual(two["tick"], str(second["tick"]))
            self.assertEqual(two["prediction"], "0.75")
            self.assertEqual(two["frames"], "2 / 2")


if __name__ == "__main__":
    unittest.main()
