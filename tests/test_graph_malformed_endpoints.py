"""Regression: untrusted graph endpoints must fail with GraphError, not TypeError."""
import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError, validate

FIXTURE = json.loads((Path(__file__).resolve().parent.parent / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class MalformedEndpointTests(unittest.TestCase):
    def test_unhashable_or_nonstring_endpoint_values_fail_closed(self):
        for endpoint in ("from", "to"):
            for field in ("node", "port"):
                for value in ([], {}, 42, None, True):
                    with self.subTest(endpoint=endpoint, field=field, value=value):
                        graph = copy.deepcopy(FIXTURE)
                        graph["edges"][0][endpoint][field] = value
                        with self.assertRaisesRegex(GraphError, "Malformed edge endpoint"):
                            validate(graph)


if __name__ == "__main__":
    unittest.main()
