import copy
import json
import unittest
from pathlib import Path
from mojum_core.graph import GraphError, validate
from mojum.graph import PORTS, validate as legacy_validate, GraphError as LegacyError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT/"legacy/examples/first_loop.json").read_text(encoding="utf-8"))

class GraphExtractionTests(unittest.TestCase):
    def test_legacy_contract_and_topology(self):
        self.assertEqual(validate(FIXTURE, ports=PORTS), legacy_validate(FIXTURE))

    def test_registry_independent_of_domain(self):
        graph = {"schema":"mechanicus.universal-graph/1","id":"demo","revision":"1",
                 "nodes":[{"id":"a","kind":"custom.source","config":{},"notes":{"purpose":"source"}},
                          {"id":"b","kind":"custom.sink","config":{},"notes":{"purpose":"sink"}}],
                 "edges":[{"from":{"node":"a","port":"out"},"to":{"node":"b","port":"in"}}]}
        registry = {"custom.source":({},{"out":"sample/1"}),"custom.sink":({"in":"sample/1"},{})}
        self.assertEqual(validate(graph,ports=registry)[2],["a","b"])
        with self.assertRaises(GraphError):
            validate(graph,ports={})
        registry["custom.sink"][0]["in"]="wrong/1"
        with self.assertRaises(GraphError):
            validate(graph,ports=registry)

    def test_legacy_rejections(self):
        cases=[]
        x=copy.deepcopy(FIXTURE); x["extra"]=True; cases.append(x)
        x=copy.deepcopy(FIXTURE); x["nodes"][0]["kind"]="unknown"; cases.append(x)
        x=copy.deepcopy(FIXTURE); x["nodes"][0]["config"]["bad"]=float("nan"); cases.append(x)
        x=copy.deepcopy(FIXTURE); x["edges"][0]["to"]["port"]="invalid"; cases.append(x)
        x=copy.deepcopy(FIXTURE); x["nodes"].append(copy.deepcopy(x["nodes"][0])); cases.append(x)
        for graph in cases:
            with self.assertRaises(LegacyError):
                legacy_validate(graph)
            with self.assertRaises(GraphError):
                validate(graph,ports=PORTS)

    def test_malformed_registry(self):
        for registry in (None,[],{"x":None},{"x":({},{"p":5})},{"x":({},{"p":"\ud800"})}):
            with self.assertRaises(GraphError):
                validate(FIXTURE,ports=registry)

if __name__ == "__main__":
    unittest.main()
