import json
import unittest
from pathlib import Path
from mojum_core.executor import Executor
from mojum_core.graph import GraphError
from mojum.graph import PORTS
from mojum.sim import Simulation

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT/"legacy/examples/first_loop.json").read_text(encoding="utf-8"))

# Legacy domain handlers are test-side composition, never core imports.
def seed(i,c,t,ns):
    bpm=c.get("bpm",60)
    if type(bpm) is not int or not 30 <= bpm <= 180:
        raise GraphError("bad bpm")
    return {"genome":{"fixture":True,"heartbeat_bpm":bpm,"genotype_status":"SEED_ONLY"}},[]
def heartbeat(i,c,t,ns):
    period=60_000_000_000//i["genome"]["heartbeat_bpm"]
    phase=ns%period
    return {"pulse":{"beat_index":ns//period,"phase_milli":1000*phase//period,"pulse":phase<period//8,"synthetic":True}},[]
def sense(i,c,t,ns):
    return {"sensory":{"kind":"interoception","heartbeat":i["pulse"],"observed_at_ns":ns}},[]
def room(i,c,t,ns):
    level=c.get("light","dim")
    if level not in ("dark","dim","bright"): raise GraphError("bad light")
    return {"world":{"kind":"static-room-fixture","light":level,"observed_at_ns":ns}},[]
def brain(i,c,t,ns):
    return {"observation":{"status":"UNIMPLEMENTED","sensory_kind":i["sensory"]["kind"],"world_light":i["world"]["light"],"action":None}},[]
def memory(i,c,t,ns):
    e={"tick":t,"observed_at_ns":ns,"sensory":i["sensory"],"brain_observation":i["observation"],"provenance":"in-memory-fixture-not-Noosphere"}
    return {"episode":e},[e]
HANDLERS={"genesis.seed":seed,"body.heartbeat":heartbeat,"sensory.interoception":sense,
          "world.room":room,"brain.placeholder":brain,"memory.episode":memory}

def generic():
    return {"schema":"mechanicus.universal-graph/1","id":"g","revision":"1",
            "nodes":[{"id":"a","kind":"demo.source","config":{},"notes":{"purpose":"source"}},
                     {"id":"z","kind":"demo.sink","config":{},"notes":{"purpose":"sink"}}],
            "edges":[{"from":{"node":"a","port":"x"},"to":{"node":"z","port":"x"}}]}
GENERIC_PORTS={"demo.source":({},{"x":"value/1"}),"demo.sink":({"x":"value/1"},{})}

class ExecutorTests(unittest.TestCase):
    def test_legacy_differential(self):
        old=Simulation(FIXTURE)
        new=Executor(FIXTURE,ports=PORTS,handlers=HANDLERS)
        events=[]
        for _ in range(120):
            result=new.step()
            self.assertEqual(result["outputs"],old.step())
            events.extend(result["effects"])
        self.assertEqual(events,old.episodes)
        self.assertEqual(new.tick,old.tick)
    def test_atomic_failure_and_retry(self):
        broken=[True]
        def source(i,c,t,ns): return {"x":{"v":t}},[{"t":t}]
        def sink(i,c,t,ns):
            if broken[0]: raise RuntimeError("late failure")
            return {},[]
        ex=Executor(generic(),ports=GENERIC_PORTS,handlers={"demo.source":source,"demo.sink":sink})
        with self.assertRaises(RuntimeError): ex.step()
        self.assertEqual(ex.tick,0)
        broken[0]=False
        self.assertEqual(ex.step()["effects"],[{"t":0}])
        self.assertEqual(ex.tick,1)
    def test_input_mutation_cannot_change_prior_output(self):
        def source(i,c,t,ns): return {"x":{"v":1}},[]
        def sink(i,c,t,ns):
            i["x"]["v"]=9
            return {},[]
        ex=Executor(generic(),ports=GENERIC_PORTS,handlers={"demo.source":source,"demo.sink":sink})
        self.assertEqual(ex.step()["outputs"]["a"]["x"],{"v":1})
    def test_invalid_ports_fail_without_advancing(self):
        ex=Executor(generic(),ports=GENERIC_PORTS,handlers={"demo.source":lambda *a:({},[]),"demo.sink":lambda *a:({},[])})
        with self.assertRaises(GraphError): ex.step()
        self.assertEqual(ex.tick,0)
