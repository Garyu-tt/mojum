"""Minimal versioned SDK for trusted, explicitly registered pure node providers.

Manifest admission does not authenticate code or provide a Python sandbox.
No discovery, import, network access or durable side effects occur here.
"""
import re
import inspect
from functools import partial
from threading import Lock
from .executor import Executor
from .graph import GraphError, _freeze_ports

API_MAJOR = 1
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})")
_SHA = re.compile(r"[0-9a-f]{40}")
_NODE = re.compile(r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})+")

def _exact(obj, fields):
    return (type(obj) is dict and len(obj) == len(fields)
            and all(type(k) is str for k in obj) and set(obj) == set(fields))

def _matches(pattern, value):
    return type(value) is str and len(value) <= 256 and pattern.fullmatch(value) is not None

def _sync_handler(fn):
    # inspect unwraps partial functions, but not partial callable instances.
    while type(fn) is partial:
        fn = fn.func
    return (callable(fn) and not inspect.iscoroutinefunction(fn)
            and not inspect.isasyncgenfunction(fn)
            and not inspect.isgeneratorfunction(fn)
            and not inspect.iscoroutinefunction(getattr(type(fn), "__call__", None))
            and not inspect.isasyncgenfunction(getattr(type(fn), "__call__", None))
            and not inspect.isgeneratorfunction(getattr(type(fn), "__call__", None)))

class Registry:
    """Publish one coherent copy-on-write snapshot per registration.

    Callers must not mutate supplied manifests/handlers concurrently.
    Registered handler execution is trusted and not sandboxed.
    """
    def __init__(self):
        self._state = ({}, {}, {})
        self._lock = Lock()

    def register(self, manifest, handlers, *, revision):
        if not _exact(manifest, {"schema","id","version","api_major","nodes"}):
            raise GraphError("Invalid plugin manifest fields")
        plugin_id = manifest["id"]
        version = manifest["version"]
        api_major = manifest["api_major"]
        if (type(manifest["schema"]) is not str or manifest["schema"] != "mojum.plugin/1"
            or not _matches(_ID,plugin_id) or not _matches(_VERSION,version)
            or type(api_major) is not int or api_major != API_MAJOR
            or not _matches(_SHA,revision)):
            raise GraphError("Incompatible or unpinned plugin manifest")
        nodes=manifest["nodes"]
        if type(nodes) is not dict or not 1 <= len(nodes) <= 256:
            raise GraphError("Invalid node declarations")
        ports={}
        for kind, declaration in nodes.items():
            if (not _matches(_NODE,kind) or not kind.startswith(plugin_id+".")
                or not _exact(declaration,{"inputs","outputs"})):
                raise GraphError("Invalid node namespace")
            ports[kind]=(declaration["inputs"],declaration["outputs"])
        ports=_freeze_ports(ports)
        if not _exact(handlers,ports):
            raise GraphError("Handler declarations do not match manifest")
        supplied = dict(handlers)
        if any(not _sync_handler(fn) for fn in supplied.values()):
            raise GraphError("Handlers must be synchronous callables")
        record={"id":plugin_id,"version":version,
                "api_major":api_major,"revision":revision}
        with self._lock:
            old_plugins, old_ports, old_handlers = self._state
            if plugin_id in old_plugins or any(k in old_ports for k in ports):
                raise GraphError("Plugin already registered")
            if len(old_ports)+len(ports)>1024:
                raise GraphError("Registry capacity exceeded")
            next_state = ({**old_plugins,plugin_id:record},
                          {**old_ports,**ports}, {**old_handlers,**supplied})
            self._state = next_state

    def inventory(self):
        with self._lock:
            plugins = self._state[0]
        return [dict(plugins[k]) for k in sorted(plugins)]

    def executor(self, graph, *, dt_ns=10_000_000):
        with self._lock:
            _, ports, handlers = self._state
        return Executor(graph,ports=ports,handlers=handlers,dt_ns=dt_ns)
