"""Load explicitly trusted single-file providers from a pinned byte bundle.

Integrity is relative to a trusted lock, not a signature or sandbox.
Never call this with a lock supplied by an untrusted plugin.
"""
import hashlib
import json
import re
import sys
from types import ModuleType
from uuid import uuid4
from .graph import GraphError
from .sdk import Registry, _exact, _matches, _SHA, _ID, _VERSION

_HEX256 = re.compile(r"[0-9a-f]{64}")
MAX_MANIFEST_BYTES = 65536
MAX_SOURCE_BYTES = 1048576
MAX_PLUGINS = 64

def _unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise GraphError("Duplicate manifest key")
        out[k] = v
    return out

def _bad_constant(value):
    raise GraphError("Nonfinite manifest value")

def _verified_bytes(value, expected, maximum):
    if (type(value) is not bytes or not 0 < len(value) <= maximum
        or not _matches(_HEX256, expected)):
        raise GraphError("Invalid artifact or hash")
    if hashlib.sha256(value).hexdigest() != expected:
        raise GraphError("Artifact digest mismatch")
    return value

def load_registry(lock, artifacts):
    """Construct a fresh registry; never update the current one in place.

    lock: trusted JSON-native release records. artifacts: mapping of ID to
    exact (manifest_bytes, source_bytes) tuples. Source defines HANDLERS.
    All hashes, manifest contracts and syntax are checked before execution.
    Python code and its imports execute with the host's permissions.
    """
    if (not _exact(lock, {"schema", "plugins"})
        or type(lock["schema"]) is not str or lock["schema"] != "mojum.release-lock/1"
        or type(lock["plugins"]) is not list or len(lock["plugins"]) > MAX_PLUGINS
        or type(artifacts) is not dict or len(artifacts) > MAX_PLUGINS
        or len(artifacts) != len(lock["plugins"])
        or any(type(k) is not str for k in artifacts)):
        raise GraphError("Invalid release lock")
    probe, prepared, ids = Registry(), [], set()
    for entry in lock["plugins"]:
        if not _exact(entry, {"id","version","revision","manifest_sha256","source_sha256"}):
            raise GraphError("Invalid release entry")
        pid = entry["id"]
        if (not _matches(_ID,pid) or not _matches(_VERSION,entry["version"])
            or not _matches(_SHA,entry["revision"]) or pid in ids):
            raise GraphError("Duplicate or invalid release pin")
        ids.add(pid)
        bundle = artifacts.get(pid)
        if type(bundle) is not tuple or len(bundle) != 2:
            raise GraphError("Missing artifact bundle")
        raw = _verified_bytes(bundle[0],entry["manifest_sha256"],MAX_MANIFEST_BYTES)
        source = _verified_bytes(bundle[1],entry["source_sha256"],MAX_SOURCE_BYTES)
        try:
            manifest = json.loads(raw.decode("utf-8"),object_pairs_hook=_unique,
                                  parse_constant=_bad_constant)
            code = compile(source.decode("utf-8"),"<mojum-plugin:"+pid+">","exec")
        except (ValueError, UnicodeError, SyntaxError, RecursionError) as exc:
            raise GraphError("Invalid plugin artifact") from exc
        if (type(manifest) is not dict or manifest.get("id") != pid
            or manifest.get("version") != entry["version"]
            or type(manifest.get("nodes")) is not dict):
            raise GraphError("Manifest does not match pinned identity")
        # Preflight with dummy callables; do not execute any plugin yet.
        probe.register(manifest,{k:lambda *args:None for k in manifest["nodes"]},
                       revision=entry["revision"])
        prepared.append((pid,entry["revision"],manifest,code))
    if set(artifacts) != ids:
        raise GraphError("Unexpected unpinned artifacts")
    registry = Registry()
    for pid,revision,manifest,code in prepared:
        # Dataclass decoration and other definition-time introspection need
        # a real module identity in sys.modules. Use a unique transient name
        # so fresh loads cannot replace one another's module during exec.
        name = "_mojum_provider_" + pid.replace("-", "_") + "_" + uuid4().hex
        module = ModuleType(name)
        try:
            sys.modules[name] = module
            exec(code,module.__dict__)
            registry.register(manifest,module.__dict__.get("HANDLERS"),revision=revision)
        finally:
            if sys.modules.get(name) is module:
                del sys.modules[name]
    return registry
