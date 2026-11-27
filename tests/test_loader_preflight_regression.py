"""A rejected later bundle must not execute any earlier provider."""
import hashlib
import json
import unittest

from mojum_core.graph import GraphError
from mojum_core.loader import load_registry


def artifact(pid, raw=None, source=None):
    if raw is None:
        raw = json.dumps({
            "schema": "mojum.plugin/1", "id": pid, "version": "1.0.0",
            "api_major": 1, "nodes": {
                pid + ".emit": {"inputs": {}, "outputs": {"x": "value/1"}}
            },
        }).encode("utf-8")
    if source is None:
        source = b"raise AssertionError('provider executed before preflight completed')"
    entry = {
        "id": pid, "version": "1.0.0", "revision": "1" * 40,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }
    return entry, (raw, source)


class LoaderPreflightRegression(unittest.TestCase):
    def test_late_artifact_rejection_precedes_all_provider_execution(self):
        first, good = artifact("first")
        second, valid = artifact("second")
        raw, src = valid
        duplicate = raw.replace(b'"api_major": 1', b'"api_major": 1, "api_major": 1')
        nested_duplicate = raw.replace(b'"x": "value/1"', b'"x": "value/1", "x": "value/1"')
        nonfinite = raw.replace(b'"api_major": 1', b'"api_major": NaN')
        cases = [
            ("source digest", second, (raw, src + b"\n")),
            ("manifest digest", second, (raw + b" ", src)),
            ("duplicate root key", *artifact("second", raw=duplicate)),
            ("duplicate nested key", *artifact("second", raw=nested_duplicate)),
            ("nonfinite constant", *artifact("second", raw=nonfinite)),
            ("invalid UTF-8 manifest", *artifact("second", raw=b"\xff")),
            ("invalid UTF-8 source", *artifact("second", source=b"\xff")),
        ]
        for name, entry, bundle in cases:
            with self.subTest(name=name):
                lock = {"schema": "mojum.release-lock/1", "plugins": [first, entry]}
                with self.assertRaises(GraphError):
                    load_registry(lock, {"first": good, "second": bundle})

    def test_positive_control_executes_provider(self):
        entry, bundle = artifact("first")
        lock = {"schema": "mojum.release-lock/1", "plugins": [entry]}
        with self.assertRaisesRegex(AssertionError, "provider executed"):
            load_registry(lock, {"first": bundle})
