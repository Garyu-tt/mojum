import copy, unittest
from mojum.genesis import ACQUISITION_SCHEMA, GenomeLedger
from mojum.genesis_phenotype_audit import MAX_INPUT_BYTES, PhenotypeAuditError, audit_phenotype_snapshot

SEED = {"schema":"mojum.genesis.genotype/1","genome_id":"public-synthetic-test","revision":"seed-r1","inherited":{"heartbeat_bpm":60,"body_scale_milli":1000,"sensory_gain_milli":1000}}
def make_ledger():
    ledger = GenomeLedger(SEED)
    event = {"schema":ACQUISITION_SCHEMA,"event_id":"event-1","genome_id":SEED["genome_id"],"gene":"heartbeat_bpm","delta":4,"valid_from_tick":5,"observed_at_ns":10,"recorded_at_ns":11,"source":"test","evidence":"PUBLIC_SYNTHETIC fixture only"}
    ledger.append(event, ledger.revision)
    return ledger

class PhenotypeAuditTests(unittest.TestCase):
    def audit(self, ledger, phenotype):
        return audit_phenotype_snapshot(ledger.snapshot(), phenotype, source_classification="PUBLIC_SYNTHETIC")
    def test_exact_recomputation_is_accepted(self):
        ledger=make_ledger(); result=self.audit(ledger, ledger.phenotype(5))
        self.assertTrue(result.accepted); self.assertEqual(result.mismatch_fields, ()); self.assertEqual(result.event_count,1)
    def test_tampered_parameter_is_rejected_and_redacted(self):
        ledger=make_ledger(); expected=ledger.phenotype(5); expected["parameters"]["heartbeat_bpm"]+=1
        result=self.audit(ledger, expected)
        self.assertFalse(result.accepted); self.assertEqual(result.mismatch_fields,("parameters",))
        self.assertNotIn("event-1",repr(result.to_dict())); self.assertNotIn("heartbeat_bpm",repr(result.to_dict()))
    def test_missing_and_extra_fields_are_rejected(self):
        ledger=make_ledger(); expected=ledger.phenotype(5); expected.pop("ledger_revision"); expected["extra"]="x"
        self.assertEqual(self.audit(ledger,expected).mismatch_fields,("extra","ledger_revision"))
    def test_wrong_tick_recomputes_at_supplied_tick(self):
        ledger=make_ledger(); expected=ledger.phenotype(5); expected["at_tick"]=4
        self.assertIn("at_tick",self.audit(ledger,expected).mismatch_fields)
    def test_tampered_ledger_rejected(self):
        ledger=make_ledger(); snapshot=ledger.snapshot(); snapshot["events"][0]["delta"]+=1
        with self.assertRaises(PhenotypeAuditError):
            audit_phenotype_snapshot(snapshot,ledger.phenotype(5),source_classification="PUBLIC_SYNTHETIC")
    def test_wrong_classification_fails_closed(self):
        ledger=make_ledger()
        with self.assertRaises(PhenotypeAuditError):
            audit_phenotype_snapshot(ledger.snapshot(),ledger.phenotype(5),source_classification="PRIVATE")
    def test_subclass_containers_rejected(self):
        class D(dict): pass
        ledger=make_ledger()
        with self.assertRaises(PhenotypeAuditError):
            audit_phenotype_snapshot(D(ledger.snapshot()),ledger.phenotype(5),source_classification="PUBLIC_SYNTHETIC")
    def test_non_string_keys_rejected(self):
        ledger=make_ledger(); snapshot=ledger.snapshot(); snapshot[object()]="x"
        with self.assertRaises(PhenotypeAuditError):
            audit_phenotype_snapshot(snapshot,ledger.phenotype(5),source_classification="PUBLIC_SYNTHETIC")
    def test_nonfinite_and_surrogate_rejected(self):
        ledger=make_ledger(); expected=ledger.phenotype(5); expected["bad"]=float("nan")
        with self.assertRaises(PhenotypeAuditError): self.audit(ledger,expected)
        expected=ledger.phenotype(5); expected["bad"]="\\ud800"
        with self.assertRaises(PhenotypeAuditError): self.audit(ledger,expected)
    def test_depth_and_byte_budgets(self):
        ledger=make_ledger(); expected=ledger.phenotype(5); cursor=expected
        for _ in range(30): cursor["nested"]={}; cursor=cursor["nested"]
        with self.assertRaises(PhenotypeAuditError): self.audit(ledger,expected)
        expected=ledger.phenotype(5); expected["padding"]="x"*MAX_INPUT_BYTES
        with self.assertRaises(PhenotypeAuditError): self.audit(ledger,expected)
    def test_result_detached_from_caller_mutations(self):
        ledger=make_ledger(); snapshot=ledger.snapshot(); expected=ledger.phenotype(5)
        result=audit_phenotype_snapshot(snapshot,expected,source_classification="PUBLIC_SYNTHETIC")
        snapshot["events"][0]["delta"]=999; expected["parameters"]["heartbeat_bpm"]=999
        self.assertTrue(result.accepted); self.assertEqual(result.at_tick,5)
    def test_digest_is_deterministic(self):
        ledger=make_ledger(); first=self.audit(ledger,ledger.phenotype(5))
        second=audit_phenotype_snapshot(copy.deepcopy(ledger.snapshot()),copy.deepcopy(ledger.phenotype(5)),source_classification="PUBLIC_SYNTHETIC")
        self.assertEqual(first.to_dict(),second.to_dict())
    def test_numerically_equal_different_json_types_are_rejected(self):
        ledger=make_ledger(); expected=ledger.phenotype(5)
        expected["parameters"]["heartbeat_bpm"] = float(expected["parameters"]["heartbeat_bpm"])
        result=self.audit(ledger, expected)
        self.assertFalse(result.accepted)
        self.assertEqual(result.mismatch_fields, ("parameters",))

if __name__ == "__main__": unittest.main()
