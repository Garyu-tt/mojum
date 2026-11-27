"""Integration against separately checked out, exact GENESIS candidate blobs."""
import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from mojum_core.loader import load_registry
from mojum.graph import GraphError as LegacyGraphError
from mojum.genesis import GenomeLedger, GenesisError

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/"plugin-candidate"
REV="32db9baef692abad68b641f1f70f7bef98897807"
PINS={
    "examples/genesis_seed.json": "4bfe4d0199cbafc1a1deb5ba5626cc0bf2f7d7aa",
    "sdk_adapter.py": "9d1d9819c2fa31cd4b69366f0bcb30fdfd9cd48e",
    "sdk_manifest.json": "8c91362107f6d4a7cd73fe3842bc6ea8acc2ccab",
    "src/mojum/genesis.py": "b10d0657a2c34cca5f221253f37e5ac516987024"
}
def git_blob(raw):
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()

def artifact():
    actual=subprocess.check_output(["git","-C",str(PLUGIN),"rev-parse","HEAD"],text=True).strip()
    if actual != REV: raise AssertionError("Unexpected plugin revision")
    data={name:(PLUGIN/name).read_bytes() for name in PINS}
    for name,raw in data.items():
        if git_blob(raw)!=PINS[name]: raise AssertionError("Plugin blob mismatch: "+name)
    raw=data["sdk_manifest.json"]
    src=data["src/mojum/genesis.py"]+b"\n\n"+data["sdk_adapter.py"]
    entry={"id":"genesis-ledger","version":"0.1.0","revision":REV,
           "manifest_sha256":hashlib.sha256(raw).hexdigest(),"source_sha256":hashlib.sha256(src).hexdigest()}
    return {"schema":"mojum.release-lock/1","plugins":[entry]}, {"genesis-ledger":(raw,src)},json.loads(data["examples/genesis_seed.json"])

def graph(snapshot):
    return {"schema":"mechanicus.universal-graph/1","id":"ledger-test","revision":"1",
            "nodes":[{"id":"g","kind":"genesis-ledger.project","config":{"ledger":snapshot},
                      "notes":{"purpose":"project original ledger"}}],"edges":[]}

class GenesisIntegration(unittest.TestCase):
    def test_original_ledger_projection(self):
        lock,bundles,seed=artifact()
        ledger=GenomeLedger(seed)
        ledger.append({"schema":"mojum.genesis.acquisition/1","event_id":"e1","genome_id":seed["genome_id"],
            "gene":"heartbeat_bpm","delta":7,"valid_from_tick":3,"observed_at_ns":0,
            "recorded_at_ns":0,"source":"test","evidence":"migration fixture"},ledger.revision)
        snap=ledger.snapshot()
        registry=load_registry(lock,bundles)
        executor=registry.executor(graph(snap))
        for tick in range(8):
            r=executor.step()
            self.assertEqual(r["outputs"]["g"]["phenotype"],ledger.phenotype(tick))
            self.assertEqual(r["outputs"]["g"]["ledger"],snap)
            self.assertEqual(r["effects"],[])
        self.assertEqual(executor.tick,8)

    def test_missing_config_does_not_advance(self):
        lock,bundles,seed=artifact()
        g=graph(GenomeLedger(seed).snapshot())
        g["nodes"][0]["config"]={}
        executor=load_registry(lock,bundles).executor(g)
        with self.assertRaisesRegex(ValueError,"requires config.ledger"):
            executor.step()
        self.assertEqual(executor.tick,0)

    def test_forged_ledger_does_not_advance(self):
        lock,bundles,seed=artifact()
        snap=GenomeLedger(seed).snapshot()
        snap["revision"]="0"*64
        executor=load_registry(lock,bundles).executor(graph(snap))
        with self.assertRaisesRegex(ValueError,"checksum mismatch"): executor.step()
        self.assertEqual(executor.tick,0)
