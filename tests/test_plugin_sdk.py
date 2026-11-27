import copy
import unittest
from functools import partial
from threading import Barrier, Thread
from unittest.mock import patch
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

    def test_parallel_registration_one_winner(self):
        r=Registry()
        barrier=Barrier(3)
        results=[]
        def worker():
            barrier.wait(3)
            try:
                r.register(SOURCE,{"source.emit":emit},revision=REV)
                results.append("ok")
            except GraphError:
                results.append("rejected")
        threads=[Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        barrier.wait(3)
        for t in threads: t.join(4)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertCountEqual(results,["ok","rejected"])
        self.assertEqual(len(r.inventory()),1)
        r.register(SINK,{"sink.read":read},revision=REV)
        self.assertEqual(r.executor(graph()).step()["effects"],[{"value":0}])

    def test_async_and_generator_handlers_rejected(self):
        async def async_handler(*args): return {},[]
        def generator(*args): yield 1
        class AsyncCallable:
            async def __call__(self,*args): return {},[]
        r=Registry()
        for fn in (async_handler,generator,AsyncCallable()):
            with self.assertRaisesRegex(GraphError,"synchronous"):
                r.register(SOURCE,{"source.emit":fn},revision=REV)
            self.assertEqual(r.inventory(),[])

    def test_publication_failure_preserves_previous_snapshot(self):
        r=Registry()
        r.register(SOURCE,{"source.emit":emit},revision=REV)
        before=r.inventory()
        with patch("mojum_core.sdk._freeze_ports",side_effect=MemoryError):
            with self.assertRaises(MemoryError):
                r.register(SINK,{"sink.read":read},revision=REV)
        self.assertEqual(r.inventory(),before)
        r.register(SINK,{"sink.read":read},revision=REV)
        self.assertEqual(r.executor(graph()).step()["effects"],[{"value":0}])

    def test_oversized_manifest_rejected(self):
        r=Registry()
        with self.assertRaises(GraphError):
            r.register(dict(SOURCE,version="1"*1000000),{"source.emit":emit},revision=REV)
        with self.assertRaises(GraphError):
            r.register(SOURCE,{"source.emit":emit,**{str(i):emit for i in range(50001)}},revision=REV)
        self.assertEqual(r.inventory(),[])

    def test_partial_callable_classification(self):
        class AsyncCallable:
            async def __call__(self, *args): return {},[]
        class AsyncGenerator:
            async def __call__(self, *args): yield 1
        class Generator:
            def __call__(self, *args): yield 1
        class SyncCallable:
            def __call__(self, *args): return {"x": args[2]},[]
        async def async_handler(*args): return {},[]
        def generator(*args): yield 1
        r=Registry()
        for fn in (partial(AsyncCallable()), partial(AsyncGenerator()),
                   partial(Generator()), partial(partial(AsyncCallable()), 1),
                   partial(async_handler), partial(generator)):
            with self.subTest(handler=fn):
                with self.assertRaisesRegex(GraphError, "synchronous"):
                    r.register(SOURCE, {"source.emit": fn}, revision=REV)
                self.assertEqual(r.inventory(), [])
        for fn in (partial(SyncCallable()), partial(emit)):
            with self.subTest(handler=fn):
                r=Registry()
                r.register(SOURCE, {"source.emit": fn}, revision=REV)
                r.register(SINK, {"sink.read": read}, revision=REV)
                self.assertEqual(r.executor(graph()).step()["effects"], [{"value": 0}])
