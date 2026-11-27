import importlib.util
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
        snapshot=ledger.snapshot()
        handler=ns["HANDLERS"]["genesis-ledger.project"]
        output,effects=handler({},{"ledger":snapshot},7,70)
        self.assertEqual(output,{"phenotype":ledger.phenotype(7),"ledger":snapshot})
        self.assertEqual(effects,[])
