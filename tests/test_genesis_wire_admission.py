import unittest, json, copy
from unittest.mock import ANY
from types import MappingProxyType
from mojum.genesis import GENOTYPE_SCHEMA, ACQUISITION_SCHEMA, GenesisError, GenomeLedger

SEED = dict(schema=GENOTYPE_SCHEMA, genome_id='g', revision='r', inherited=dict(heartbeat_bpm=60, body_scale_milli=1000, sensory_gain_milli=1000))
class Forged(str):
    def __eq__(self, other): return True
    def __ne__(self, other): return False
    __hash__ = str.__hash__

def event():
    return dict(schema=ACQUISITION_SCHEMA,event_id='e',genome_id='g',gene='heartbeat_bpm',delta=1,valid_from_tick=2,observed_at_ns=1,recorded_at_ns=2,source='fixture',evidence='fixture')

class Probe(unittest.TestCase):
    def test_native_roundtrip(self):
        ledger=GenomeLedger(SEED); ledger.append(event(), ledger.revision)
        restored=GenomeLedger.from_snapshot(json.loads(json.dumps(ledger.snapshot())))
        self.assertEqual(ledger.snapshot(),restored.snapshot())
        self.assertEqual(ledger.phenotype(2),restored.phenotype(2))
    def test_wrong_native_revision(self):
        s=GenomeLedger(SEED).snapshot();s['revision']='bad'
        with self.assertRaises(GenesisError): GenomeLedger.from_snapshot(s)
    def test_non_native_revision(self):
        s=GenomeLedger(SEED).snapshot();s['revision']=ANY
        with self.assertRaises(GenesisError): GenomeLedger.from_snapshot(s)
    def test_foreign_genome_no_mutation(self):
        ledger=GenomeLedger(SEED); e=event();e['genome_id']=Forged('foreign'); before=ledger.snapshot()
        with self.assertRaises(GenesisError): ledger.append(e,ledger.revision)
        self.assertEqual(ledger.snapshot(),before)
    def test_invalid_schema(self):
        s=copy.deepcopy(SEED);s['schema']=Forged('wrong')
        with self.assertRaises(GenesisError): GenomeLedger(s)
    def test_readonly_mapping(self):
        s=dict(SEED,inherited=MappingProxyType(SEED['inherited']))
        self.assertEqual(GenomeLedger(s).snapshot()['seed'],SEED)
    def test_wrong_native_genome(self):
        ledger=GenomeLedger(SEED); e=event();e['genome_id']='foreign'; before=ledger.snapshot()
        with self.assertRaises(GenesisError): ledger.append(e,ledger.revision)
        self.assertEqual(ledger.snapshot(),before)
if __name__=='__main__': unittest.main(verbosity=2)
