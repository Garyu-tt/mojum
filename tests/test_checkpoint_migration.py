import copy
import json
import unittest
from pathlib import Path
from mojum_compat.legacy_sim import LegacySimulation, digest, MAX_REPLAY_TICKS
from mojum_core.graph import GraphError
from mojum.sim import Simulation

ROOT=Path(__file__).resolve().parents[1]
GRAPH=json.loads((ROOT/"legacy/examples/first_loop.json").read_text(encoding="utf-8"))

def graph_sinks(n):
    g=copy.deepcopy(GRAPH)
    if n==0:
        g["nodes"]=[x for x in g["nodes"] if x["id"]!="episode"]
        g["edges"]=[x for x in g["edges"] if x["to"]["node"]!="episode"]
    if n==2:
        node=copy.deepcopy(next(x for x in g["nodes"] if x["id"]=="episode"))
        node["id"]="episode_copy"
        g["nodes"].append(node)
        for e in list(g["edges"]):
            if e["to"]["node"]=="episode":
                e=copy.deepcopy(e)
                e["to"]["node"]="episode_copy"
                g["edges"].append(e)
    return g

class CheckpointCompatibility(unittest.TestCase):
    def test_bidirectional_legacy_restore(self):
        for n in (0,1,2):
            with self.subTest(sinks=n):
                g=graph_sinks(n)
                old=Simulation(g)
                for _ in range(7): old.step()
                new=LegacySimulation(g)
                new.restore(old.snapshot())
                self.assertEqual(new.snapshot(),old.snapshot())
                for _ in range(11): self.assertEqual(new.step(),old.step())
                self.assertEqual(new.snapshot(),old.snapshot())
                cold=Simulation(g)
                cold.restore(new.snapshot())
                self.assertEqual(cold.step(),new.step())
                self.assertEqual(cold.snapshot(),new.snapshot())

    def test_tampered_rehashed_history_is_atomic(self):
        sim=LegacySimulation(graph_sinks(2))
        for _ in range(3): sim.step()
        before=sim.snapshot()
        for mutation in (
            lambda s:s["episodes"][1]["brain_observation"].update(action={"dx":1}),
            lambda s:s["episodes"][0].update(observed_at_ns=False),
            lambda s:s.update(graph_sha256="0"*64),
            lambda s:s.update(tick=True),
        ):
            forged=copy.deepcopy(before)
            mutation(forged["state"])
            forged["sha256"]=digest(forged["state"])
            with self.assertRaises(GraphError): sim.restore(forged)
            self.assertEqual(sim.snapshot(),before)

    def test_failed_zero_sink_replay_and_budget(self):
        g=graph_sinks(0)
        next(n for n in g["nodes"] if n["id"]=="room")["config"]["light"]="invalid"
        sim=LegacySimulation(g)
        before=sim.snapshot()
        for tick in (1,MAX_REPLAY_TICKS+1):
            forged=copy.deepcopy(before)
            forged["state"]["tick"]=tick
            forged["sha256"]=digest(forged["state"])
            with self.assertRaises(GraphError): sim.restore(forged)
            self.assertEqual(sim.snapshot(),before)

    def test_late_failure_does_not_append_history(self):
        g=graph_sinks(1)
        g["nodes"].append({"id":"zzz","kind":"world.room","config":{"light":"invalid"},"notes":{"purpose":"late"}})
        sim=LegacySimulation(g)
        before=sim.snapshot()
        with self.assertRaises(GraphError): sim.step()
        self.assertEqual(sim.snapshot(),before)
