import copy
import json
import unittest
from threading import Event, Thread
from unittest.mock import patch
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

    def test_step_cannot_create_unrestorable_tick(self):
        with patch("mojum_compat.legacy_sim.MAX_REPLAY_TICKS",3):
            for sinks in (0,1,2):
                with self.subTest(sinks=sinks):
                    g=graph_sinks(sinks)
                    sim=LegacySimulation(g)
                    for _ in range(3): sim.step()
                    before=sim.snapshot()
                    with self.assertRaisesRegex(GraphError,"replay budget exhausted"):
                        sim.step()
                    self.assertEqual(sim.snapshot(),before)
                    restored=LegacySimulation(g)
                    restored.restore(before)
                    self.assertEqual(restored.snapshot(),before)

    def test_snapshot_waits_for_complete_step(self):
        sim=LegacySimulation(graph_sinks(1))
        entered,release,started,finished=Event(),Event(),Event(),Event()
        original=sim._executor.step
        result=[]
        errors=[]
        def delayed():
            out=original()
            entered.set()
            if not release.wait(3): raise RuntimeError("test timeout")
            return out
        def stepping():
            try: sim.step()
            except Exception as exc: errors.append(exc)
        def capture():
            started.set()
            try: result.append(sim.snapshot())
            except Exception as exc: errors.append(exc)
            finally: finished.set()
        with patch.object(sim._executor,"step",side_effect=delayed):
            worker=Thread(target=stepping)
            reader=Thread(target=capture)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                reader.start()
                self.assertTrue(started.wait(3))
                self.assertFalse(finished.wait(.05))
            finally:
                release.set()
                worker.join(4)
                if reader.ident is not None: reader.join(4)
        self.assertFalse(worker.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(result,[sim.snapshot()])
        self.assertEqual(result[0]["state"]["tick"],1)
        self.assertEqual(len(result[0]["state"]["episodes"]),1)
        restored=LegacySimulation(graph_sinks(1))
        restored.restore(result[0])
        self.assertEqual(restored.snapshot(),result[0])
