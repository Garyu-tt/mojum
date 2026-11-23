import json
import tempfile
import unittest
from pathlib import Path

from mojum.host_doctor import diagnose, _check_genome, _check_graph

GENOME = b'{"schema":"mojum.genesis.genotype/1","genome_id":"fixture","revision":"r1","inherited":{"heartbeat_bpm":60,"body_scale_milli":1000,"sensory_gain_milli":1000}}'
GRAPH = b'''{
"schema":"mechanicus.universal-graph/1","id":"fixture","revision":"r1",
"nodes":[
{"id":"g","kind":"genesis.seed","config":{},"notes":{"purpose":"fixture"}},
{"id":"h","kind":"body.heartbeat","config":{},"notes":{"purpose":"fixture"}},
{"id":"s","kind":"sensory.interoception","config":{},"notes":{"purpose":"fixture"}},
{"id":"w","kind":"world.room","config":{},"notes":{"purpose":"fixture"}},
{"id":"b","kind":"brain.placeholder","config":{},"notes":{"purpose":"fixture"}},
{"id":"m","kind":"memory.episode","config":{},"notes":{"purpose":"fixture"}}
],
"edges":[
{"from":{"node":"g","port":"genome"},"to":{"node":"h","port":"genome"}},
{"from":{"node":"h","port":"pulse"},"to":{"node":"s","port":"pulse"}},
{"from":{"node":"s","port":"sensory"},"to":{"node":"b","port":"sensory"}},
{"from":{"node":"w","port":"world"},"to":{"node":"b","port":"world"}},
{"from":{"node":"s","port":"sensory"},"to":{"node":"m","port":"sensory"}},
{"from":{"node":"b","port":"observation"},"to":{"node":"m","port":"observation"}}
]}'''

class HostDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, genome=GENOME, graph=GRAPH):
        (self.root / "genesis_seed.json").write_bytes(genome)
        (self.root / "first_loop.json").write_bytes(graph)

    def test_installed_package_fixtures_pass(self):
        report = diagnose()
        self.assertEqual(report["schema"], "mojum.host-doctor.report/1")
        self.assertEqual(report["status"], "pass")
        self.assertEqual([c["id"] for c in report["checks"]], ["packaged_genome", "packaged_graph"])

    def test_valid_synthetic_resource_root_passes(self):
        self.write()
        self.assertEqual(diagnose(resource_root=self.root)["status"], "pass")

    def test_missing_resource_is_stable_and_does_not_disclose_path(self):
        report = diagnose(resource_root=self.root)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["checks"][0]["code"], "RESOURCE_UNAVAILABLE")
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_duplicate_json_key_rejected(self):
        self.write(genome=b'{"schema":"x","schema":"y"}')
        self.assertEqual(_check_genome(self.root)["code"], "INVALID_JSON")

    def test_invalid_utf8_rejected(self):
        self.write(genome=b'\xff')
        self.assertEqual(_check_genome(self.root)["code"], "INVALID_UTF8")

    def test_nonfinite_json_rejected(self):
        self.write(genome=GENOME.replace(b'"heartbeat_bpm":60', b'"heartbeat_bpm":NaN'))
        self.assertEqual(_check_genome(self.root)["code"], "INVALID_JSON")

    def test_oversized_genome_rejected_before_decode(self):
        self.write(genome=b" " * (16 * 1024 + 1))
        self.assertEqual(_check_genome(self.root)["code"], "RESOURCE_TOO_LARGE")

    def test_invalid_genesis_contract_has_stable_code(self):
        self.write(genome=GENOME.replace(b'"heartbeat_bpm":60', b'"heartbeat_bpm":true'))
        self.assertEqual(_check_genome(self.root)["code"], "GENESIS_CONTRACT_REJECTED")

    def test_invalid_graph_contract_has_stable_code(self):
        self.write(graph=GRAPH.replace(b'"kind":"brain.placeholder"', b'"kind":"unknown.kind"'))
        self.assertEqual(_check_graph(self.root)["code"], "GRAPH_CONTRACT_REJECTED")

    def test_duplicate_graph_key_rejected(self):
        self.write(graph=b'{"schema":"x","schema":"y"}')
        self.assertEqual(_check_graph(self.root)["code"], "INVALID_JSON")

    def test_report_is_deterministic_and_redacted(self):
        self.write()
        first = diagnose(resource_root=self.root)
        self.assertEqual(first, diagnose(resource_root=self.root))
        rendered = json.dumps(first, sort_keys=True)
        self.assertNotIn("heartbeat_bpm", rendered)
        self.assertNotIn(str(self.root), rendered)

if __name__ == "__main__":
    unittest.main()
