"""Read-only diagnostics for Mojum's packaged synthetic Aquarium fixtures."""
from __future__ import annotations
import json
from importlib.resources import files
from .genesis import GenesisError, validate_seed
from .graph import GraphError, validate as validate_graph

SCHEMA = "mojum.host-doctor.report/1"
MAX_GENOME_BYTES = 16 * 1024
MAX_GRAPH_BYTES = 256 * 1024

class _DuplicateKey(ValueError):
    pass

def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = item
    return value

def _reject_constant(_token):
    raise ValueError("non-finite JSON number")

def _read(root, name, limit):
    try:
        raw = root.joinpath(name).read_bytes()
    except (OSError, TypeError, AttributeError):
        return None, "RESOURCE_UNAVAILABLE"
    if len(raw) > limit:
        return None, "RESOURCE_TOO_LARGE"
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"),
                           object_pairs_hook=_pairs,
                           parse_constant=_reject_constant)
    except UnicodeDecodeError:
        return None, "INVALID_UTF8"
    except (ValueError, RecursionError):
        return None, "INVALID_JSON"
    return value, None

def _check_genome(root):
    value, error = _read(root, "genesis_seed.json", MAX_GENOME_BYTES)
    if error:
        return {"id": "packaged_genome", "status": "fail", "code": error}
    try:
        validate_seed(value)
    except (GenesisError, TypeError, RecursionError):
        return {"id": "packaged_genome", "status": "fail", "code": "GENESIS_CONTRACT_REJECTED"}
    return {"id": "packaged_genome", "status": "pass", "code": "OK"}

def _check_graph(root):
    value, error = _read(root, "first_loop.json", MAX_GRAPH_BYTES)
    if error:
        return {"id": "packaged_graph", "status": "fail", "code": error}
    try:
        validate_graph(value)
    except (GraphError, TypeError, RecursionError):
        return {"id": "packaged_graph", "status": "fail", "code": "GRAPH_CONTRACT_REJECTED"}
    return {"id": "packaged_graph", "status": "pass", "code": "OK"}

def diagnose(*, resource_root=None):
    """Return two bounded fixture checks. resource_root is for isolated tests only."""
    root = files("mojum").joinpath("data") if resource_root is None else resource_root
    checks = [_check_genome(root), _check_graph(root)]
    return {
        "schema": SCHEMA,
        "status": "pass" if all(item["status"] == "pass" for item in checks) else "fail",
        "checks": checks,
        "scope": "PACKAGED_FIXTURES_ONLY_NO_JOURNAL_OPENED",
    }

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Read-only Mojum packaged-fixture diagnostic")
    parser.parse_args(argv)
    report = diagnose()
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if report["status"] == "pass" else 1

if __name__ == "__main__":
    raise SystemExit(main())
