import copy, hashlib, json, unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from mojum.genesis import GenomeLedger

ns = {}
root = Path(__file__).resolve().parents[1]
exec(compile((root/'src/mojum/genesis.py').read_bytes()+b'\n\n'+(root/'sdk_adapter.py').read_bytes(), '<verified provider>', 'exec'), ns)
handler = ns['HANDLERS']['genesis-ledger.project']
class Projection(unittest.TestCase):
    def setUp(self):
        seed = {'schema':'mojum.genesis.genotype/1','genome_id':'test','revision':'r1','inherited':{'heartbeat_bpm':60,'body_scale_milli':1000,'sensory_gain_milli':1000}}
        l = GenomeLedger(seed)
        for i,(tick,delta) in enumerate([(5,7),(2,-3),(5,4)]):
            l.append({'schema':'mojum.genesis.acquisition/1','event_id':f'e{i}','genome_id':'test','gene':'heartbeat_bpm','delta':delta,'valid_from_tick':tick,'observed_at_ns':i,'recorded_at_ns':i,'source':'test','evidence':'fixture'},l.revision)
        self.snap = l.snapshot()
    def call(self,tick,**extra):
        return handler({},dict(ledger=self.snap,**extra),tick,999)
    def test_temporal_projection(self):
        for tick,bpm,ids in [(0,60,[]),(1,60,[]),(2,57,['e1']),(4,57,['e1']),(5,68,['e0','e1','e2']),(2**63-1,68,['e0','e1','e2'])]:
            with self.subTest(tick=tick):
                out,effects=self.call(tick)
                self.assertEqual((out['phenotype']['parameters']['heartbeat_bpm'],out['phenotype']['applied_acquisition_ids']),(bpm,ids))
                self.assertEqual(out['phenotype']['at_tick'],tick)
                self.assertEqual(out['ledger'],self.snap)
                self.assertEqual(effects,[])
    def test_no_alias(self):
        before=copy.deepcopy(self.snap)
        out,effects=self.call(5)
        out['phenotype']['parameters']['heartbeat_bpm']=0
        out['phenotype']['applied_acquisition_ids'].clear()
        out['ledger']['seed']['inherited']['heartbeat_bpm']=0
        out['ledger']['events'][0]['delta']=0
        effects.append('mutation')
        self.assertEqual(self.snap,before)
        out,effects=self.call(5)
        self.assertEqual(out['phenotype']['parameters']['heartbeat_bpm'],68)
        self.assertEqual(out['ledger'],before)
        self.assertEqual(effects,[])
    def test_parallel_repeatability(self):
        before=copy.deepcopy(self.snap)
        expected=[self.call(i%7) for i in range(100)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            actual=list(pool.map(lambda i:self.call(i%7),range(100)))
        self.assertEqual(actual,expected)
        self.assertEqual(self.snap,before)
    def test_fail_then_retry(self):
        before=copy.deepcopy(self.snap)
        for tick in (-1,True,2**63,1.5):
            with self.subTest(tick=tick),self.assertRaises(ns['GenesisError']): self.call(tick)
        self.assertEqual(self.snap,before)
        self.assertEqual(self.call(5)[0]['phenotype']['parameters']['heartbeat_bpm'],68)
    def test_contract_revision(self):
        out,effects=self.call(5,ignored='extra config')
        self.assertEqual(set(out),{'phenotype','ledger'})
        self.assertEqual(out['phenotype']['schema'],'mojum.genesis.phenotype/1')
        self.assertEqual(out['ledger']['schema'],'mojum.genesis.ledger/1')
        wire=json.dumps({'seed':self.snap['seed'],'events':self.snap['events']},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        self.assertEqual(out['phenotype']['ledger_revision'],hashlib.sha256(wire).hexdigest())
        self.assertEqual(out['ledger']['revision'],out['phenotype']['ledger_revision'])
        self.assertEqual((out,effects),self.call(5))
if __name__=='__main__': unittest.main(verbosity=2)
