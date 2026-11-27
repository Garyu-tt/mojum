import copy
import json
from pathlib import Path
import unittest
from mojum.genesis import GenomeLedger

ROOT=Path(__file__).resolve().parents[1]
class AdapterTests(unittest.TestCase):
    def test_projection_uses_original_ledger(self):
        source=(ROOT/"src/mojum/genesis.py").read_bytes()+b"\n\n"+(ROOT/"sdk_adapter.py").read_bytes()
        ns={}
        exec(compile(source,"<trusted-test-bundle>","exec"),ns)
        seed=json.loads((ROOT/"examples/genesis_seed.json").read_text())
        ledger=GenomeLedger(seed)
        ledger.append({"schema":"mojum.genesis.acquisition/1","event_id":"e1","genome_id":seed["genome_id"],
                       "gene":"heartbeat_bpm","delta":7,"valid_from_tick":3,"observed_at_ns":0,
                       "recorded_at_ns":0,"source":"test","evidence":"migration fixture"},ledger.revision)
        snapshot=ledger.snapshot()
        before=copy.deepcopy(snapshot)
        handler=ns["HANDLERS"]["genesis-ledger.project"]
        output,effects=handler({},{"ledger":snapshot},7,70)
        self.assertEqual(snapshot,before)
        self.assertEqual(output,{"phenotype":ledger.phenotype(7),"ledger":before})
        self.assertNotEqual(output["phenotype"],GenomeLedger(seed).phenotype(7))
        for config in ({},None,{"ledger":None}):
            with self.assertRaisesRegex(ns["GenesisError"],"requires config.ledger"):
                handler({},config,7,70)
        self.assertEqual(effects,[])
