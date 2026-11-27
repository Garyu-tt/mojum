import copy
import unittest
from mojum_core.sdk import Registry
from mojum_core.graph import GraphError

REV="1"*40
def manifest(pid, nodes):
    return {"schema":"mojum.plugin/1","id":pid,"version":"1.0.0","api_major":1,"nodes":nodes}
SOURCE=manifest("source",{"source.emit":{"inputs":{},"outputs":{"x":"value/1"}}})
SINK=manifest("sink",{"sink.read":{"inputs":{"x":"value/1"},"outputs":{}}})
def emit(i,c,t,ns): return {"x":t},[]
def read(i,c,t,ns): return {},[{"value":i["x"]}]
def graph():
    return {"schema":"mechanicus.universal-graph/1","id":"demo","revision":"1",
        "nodes":[{"id":"a","kind":"source.emit","config":{},"notes":{"purpose":"emit"}},
                 {"id":"b","kind":"sink.read","config":{},"notes":{"purpose":"read"}}],
        "edges":[{"from":{"node":"a","port":"x"},"to":{"node":"b","port":"x"}}]}

class SDKTests(unittest.TestCase):
    def test_independent_providers(self):
        r=Registry()
        r.register(SOURCE,{"source.emit":emit},revision=REV)
        r.register(SINK,{"sink.read":read},revision="2"*40)
        e=r.executor(graph())
        self.assertEqual(e.step()["effects"],[{"value":0}])
        self.assertEqual(e.step()["effects"],[{"value":1}])
        self.assertEqual([x["id"] for x in r.inventory()],["sink","source"])

    def test_reject_incompatible_and_unpinned(self):
        r=Registry()
        for changed in (dict(SOURCE,api_major=2),dict(SOURCE,api_major=True),
                        dict(SOURCE,version="latest"),dict(SOURCE,id="other")):
            with self.assertRaises(GraphError):
                r.register(changed,{"source.emit":emit},revision=REV)
            self.assertEqual(r.inventory(),[])
        with self.assertRaises(GraphError):
            r.register(SOURCE,{"source.emit":emit},revision="main")

    def test_atomic_invalid_handler_set(self):
        r=Registry()
        with self.assertRaises(GraphError):
            r.register(SOURCE,{},revision=REV)
        self.assertEqual(r.inventory(),[])
        r.register(SOURCE,{"source.emit":emit},revision=REV)
        with self.assertRaises(GraphError):
            r.register(SOURCE,{"source.emit":emit},revision=REV)
        self.assertEqual(len(r.inventory()),1)

    def test_registry_detaches_declarations(self):
        r=Registry()
        m=copy.deepcopy(SOURCE)
        handlers={"source.emit":emit}
        r.register(m,handlers,revision=REV)
        m["nodes"]["source.emit"]["outputs"]["x"]="wrong/1"
        handlers.clear()
        r.register(SINK,{"sink.read":read},revision=REV)
        r.inventory()[0]["revision"]="bad"
        self.assertEqual(r.executor(graph()).step()["effects"],[{"value":0}])
        self.assertEqual(r.inventory()[0]["revision"],REV)

    def test_mismatched_port_version(self):
        r=Registry()
        r.register(SOURCE,{"source.emit":emit},revision=REV)
        m=copy.deepcopy(SINK)
        m["nodes"]["sink.read"]["inputs"]["x"]="value/2"
        r.register(m,{"sink.read":read},revision=REV)
        with self.assertRaises(GraphError): r.executor(graph())
