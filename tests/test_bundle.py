import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from build_bundle import bundle

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ('sdk_manifest.json', 'src/mojum/genesis.py', 'sdk_adapter.py')

class BundleTests(unittest.TestCase):
    def test_exact_bytes_and_repeatability(self):
        original = {p: (ROOT / p).read_bytes() for p in INPUTS}
        expected = (original[INPUTS[0]], original[INPUTS[1]] + b'\n\n' + original[INPUTS[2]])
        self.assertEqual(bundle(ROOT), expected)
        self.assertEqual(bundle(str(ROOT)), expected)
        self.assertEqual({p: (ROOT / p).read_bytes() for p in INPUTS}, original)

    def test_relocated_tree_and_missing_input(self):
        with tempfile.TemporaryDirectory() as temp:
            other = Path(temp)
            for p in INPUTS:
                (other / p).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / p, other / p)
            self.assertEqual(bundle(other), bundle(ROOT))
            for p in INPUTS:
                (other / p).unlink()
                with self.assertRaises(FileNotFoundError):
                    bundle(other)
                shutil.copyfile(ROOT / p, other / p)

    def test_manifest_and_independent_projection(self):
        manifest, source = bundle(ROOT)
        spec = json.loads(manifest)
        seed = json.loads((ROOT / 'examples/genesis_seed.json').read_bytes())
        ns = {}
        exec(compile(source, '<bundle>', 'exec'), ns)
        self.assertEqual(set(ns['HANDLERS']), set(spec['nodes']))
        ledger = ns['GenomeLedger'](seed)
        ledger.append({'schema':'mojum.genesis.acquisition/1','event_id':'e1','genome_id':seed['genome_id'],'gene':'heartbeat_bpm','delta':7,'valid_from_tick':3,'observed_at_ns':0,'recorded_at_ns':0,'source':'test','evidence':'bundle fixture'}, ledger.revision)
        snapshot = ledger.snapshot()
        before = copy.deepcopy(snapshot)
        for tick, bpm in [(0,60),(3,67),(7,67)]:
            outputs, effects = ns['HANDLERS']['genesis-ledger.project']({}, {'ledger':snapshot}, tick, tick*10)
            self.assertEqual(effects, [])
            self.assertEqual(set(outputs), set(spec['nodes']['genesis-ledger.project']['outputs']))
            for name, schema in spec['nodes']['genesis-ledger.project']['outputs'].items():
                self.assertEqual(outputs[name]['schema'], schema)
            self.assertEqual(outputs['phenotype']['parameters']['heartbeat_bpm'], bpm)
            self.assertEqual(outputs['ledger'], before)
        self.assertEqual(snapshot, before)

    def test_no_package_import_and_hashseed_invariance(self):
        manifest, source = bundle(ROOT)
        seed = (ROOT / 'examples/genesis_seed.json').read_bytes()
        runner = '''import json, sys, importlib.util
from pathlib import Path
assert importlib.util.find_spec('mojum') is None
ns = {}
exec(compile(Path('provider.py').read_bytes(), '<bundle>', 'exec'), ns)
seed = json.loads(Path('seed.json').read_bytes())
ledger = ns['GenomeLedger'](seed)
print(json.dumps(ns['HANDLERS']['genesis-ledger.project']({}, {'ledger': ledger.snapshot()}, 0, 0), sort_keys=True))
assert not any(n == 'mojum' or n.startswith('mojum.') for n in sys.modules)
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'provider.py').write_bytes(source)
            (root/'seed.json').write_bytes(seed)
            results = [subprocess.check_output([sys.executable, '-S', '-c', runner], cwd=temp, env={**os.environ, 'PYTHONPATH':'', 'PYTHONHASHSEED':str(s)}) for s in (1, 42, 123)]
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0], results[2])

if __name__ == '__main__':
    unittest.main()
