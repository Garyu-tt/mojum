"""Fail-closed graph input decoding regression (CPU/stdlib only)."""

from pathlib import Path
import tempfile
import unittest

from mojum.graph import GraphError, load_graph


class GraphInvalidEncodingTests(unittest.TestCase):
    def test_invalid_utf8_is_graph_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "invalid-graph.json"
            path.write_bytes(b'{"schema":"mechanicus.universal-graph/1","id":"\xff"}')
            with self.assertRaises(GraphError) as error:
                load_graph(path)
            self.assertIn("Malformed graph JSON", str(error.exception))

    def test_valid_utf8_but_malformed_json_is_graph_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "invalid-graph.json"
            path.write_text('{"schema":', encoding="utf-8")
            with self.assertRaises(GraphError):
                load_graph(path)


if __name__ == "__main__":
    unittest.main()
