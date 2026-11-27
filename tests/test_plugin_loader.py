import copy
import hashlib
import json
import unittest
import sys
from mojum_core.loader import load_registry
from mojum_core.graph import GraphError

def bundle(pid, source, *, version="1.0.0", api=1):
    manifest={"schema":"mojum.plugin/1","id":pid,"version":version,
              "api_major":api,"nodes":{pid+".emit":{"inputs":{},"outputs":{"x":"value/1"}}}}
    raw=json.dumps(manifest).encode()
    src=source.encode()
    entry={"id":pid,"version":version,"revision":"1"*40,
           "manifest_sha256":hashlib.sha256(raw).hexdigest(),
           "source_sha256":hashlib.sha256(src).hexdigest()}
    return entry,(raw,src)

def source(pid,value):
    return "def emit(i,c,t,ns): return {'x':"+str(value)+"}, []\nHANDLERS={"+repr(pid+".emit")+":emit}\n"

def lock(*entries): return {"schema":"mojum.release-lock/1","plugins":list(entries)}
def graph(pid):
    return {"schema":"mechanicus.universal-graph/1","id":"test","revision":"1",
        "nodes":[{"id":"a","kind":pid+".emit","config":{},"notes":{"purpose":"demo"}}],"edges":[]}

class LoaderTests(unittest.TestCase):
    def test_pinned_load_and_rollback(self):
        old,a=bundle("one",source("one",1))
        new,b=bundle("one",source("one",2),version="1.0.1")
        r1=load_registry(lock(old),{"one":a})
        r2=load_registry(lock(new),{"one":b})
        self.assertEqual(r1.executor(graph("one")).step()["outputs"]["a"]["x"],1)
        self.assertEqual(r2.executor(graph("one")).step()["outputs"]["a"]["x"],2)
        rolled_back=load_registry(lock(old),{"one":a})
        self.assertEqual(rolled_back.inventory(),r1.inventory())
        self.assertEqual(rolled_back.executor(graph("one")).step()["outputs"]["a"]["x"],1)
        self.assertEqual(r2.executor(graph("one")).step()["outputs"]["a"]["x"],2)

    def test_tampered_code_rejected_before_execution(self):
        e,a=bundle("one",source("one",1))
        with self.assertRaisesRegex(GraphError,"digest mismatch"):
            load_registry(lock(e),{"one":(a[0],b"raise AssertionError('executed')")})

    def test_preflight_entire_bundle(self):
        e,a=bundle("one","raise AssertionError('must not execute')")
        f,b=bundle("two",source("two",2),api=2)
        with self.assertRaises(GraphError): load_registry(lock(e,f),{"one":a,"two":b})

    def test_manifest_identity_and_duplicate(self):
        e,a=bundle("one",source("one",1))
        bad=dict(e,version="9.0.0")
        with self.assertRaises(GraphError): load_registry(lock(bad),{"one":a})
        with self.assertRaises(GraphError): load_registry(lock(e,e),{"one":a})
        with self.assertRaises(GraphError): load_registry(lock(e),{"one":a,"extra":a})

    def test_invalid_handler_set(self):
        e,a=bundle("one","HANDLERS={}")
        with self.assertRaises(GraphError): load_registry(lock(e),{"one":a})

    def test_syntax_error_preflight(self):
        e,a=bundle("one","raise AssertionError('must not execute')")
        f,b=bundle("two","def (")
        with self.assertRaises(GraphError): load_registry(lock(e,f),{"one":a,"two":b})

    def test_dataclass_provider_definition_and_module_cleanup(self):
        code = """from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar
@dataclass
class Value:
    constant: ClassVar[int] = 7
    x: int = 3
def emit(i,c,t,ns):
    return {"x": Value().x + Value.constant}, []
HANDLERS = {"one.emit": emit}
"""
        e,a=bundle("one",code)
        before={k for k in sys.modules if k.startswith("_mojum_provider_")}
        r=load_registry(lock(e),{"one":a})
        self.assertEqual(r.executor(graph("one")).step()["outputs"]["a"]["x"],10)
        self.assertEqual({k for k in sys.modules if k.startswith("_mojum_provider_")},before)

    def test_failed_provider_does_not_leave_module_entry(self):
        before={k for k in sys.modules if k.startswith("_mojum_provider_")}
        e,a=bundle("one","raise RuntimeError('provider failure')")
        with self.assertRaisesRegex(RuntimeError,"provider failure"):
            load_registry(lock(e),{"one":a})
        self.assertEqual({k for k in sys.modules if k.startswith("_mojum_provider_")},before)

    def test_artifact_map_size_rejected_before_provider_execution(self):
        e,a=bundle("one","raise AssertionError('must not execute')")
        with self.assertRaises(GraphError):
            load_registry(lock(e),{"one":a,**{str(i):a for i in range(65)}})
