"""Minimal versioned SDK for trusted, explicitly registered pure node providers.

Manifest admission does not authenticate code or provide a Python sandbox.
No discovery, import, network access or durable side effects occur here.
"""
import re
from .executor import Executor
from .graph import GraphError, _freeze_ports

API_MAJOR = 1
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})")
_SHA = re.compile(r"[0-9a-f]{40}")
_NODE = re.compile(r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})+")

def _exact(obj, fields):
    return type(obj) is dict and all(type(k) is str for k in obj) and set(obj) == set(fields)

def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None

class Registry:
    """One pinned provider per namespace; registration is all-or-nothing."""
    def __init__(self):
        self._plugins = {}
        self._ports = {}
        self._handlers = {}

    def register(self, manifest, handlers, *, revision):
        if not _exact(manifest, {"schema","id","version","api_major","nodes"}):
            raise GraphError("Invalid plugin manifest fields")
        if (type(manifest["schema"]) is not str or manifest["schema"] != "mojum.plugin/1"
            or not _matches(_ID,manifest["id"]) or not _matches(_VERSION,manifest["version"])
            or type(manifest["api_major"]) is not int or manifest["api_major"] != API_MAJOR
            or not _matches(_SHA,revision)):
            raise GraphError("Incompatible or unpinned plugin manifest")
        plugin_id=manifest["id"]
        if plugin_id in self._plugins:
            raise GraphError("Plugin already registered")
        nodes=manifest["nodes"]
        if type(nodes) is not dict or not 1 <= len(nodes) <= 256:
            raise GraphError("Invalid node declarations")
        ports={}
        for kind, declaration in nodes.items():
            if (not _matches(_NODE,kind) or not kind.startswith(plugin_id+".")
                or kind in self._ports or not _exact(declaration,{"inputs","outputs"})):
                raise GraphError("Invalid or conflicting node namespace")
            ports[kind]=(declaration["inputs"],declaration["outputs"])
        ports=_freeze_ports(ports)
        if not _exact(handlers,ports) or any(not callable(fn) for fn in handlers.values()):
            raise GraphError("Handler declarations do not match manifest")
        if len(self._ports)+len(ports)>1024:
            raise GraphError("Registry capacity exceeded")
        self._plugins[plugin_id]={"id":plugin_id,"version":manifest["version"],
                                  "api_major":API_MAJOR,"revision":revision}
        self._ports.update(ports)
        self._handlers.update(handlers)

    def inventory(self):
        return [dict(self._plugins[k]) for k in sorted(self._plugins)]

    def executor(self, graph, *, dt_ns=10_000_000):
        return Executor(graph,ports=self._ports,handlers=self._handlers,dt_ns=dt_ns)
