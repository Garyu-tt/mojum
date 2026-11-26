"""Hermetic UI handoff tests, independent of browser frameworks."""
import copy
import unittest

from mojum.graph import GraphError
from mojum.graph_ui_handoff import export_handoff, import_handoff


def fixture():
    return {"schema": "mechanicus.universal-graph/1", "id": "test",
            "revision": "r1", "nodes": [
                {"id": "seed", "kind": "genesis.seed", "config": {},
                 "notes": {"purpose": "source", "ui": {"x": 2, "nested": [1, 2]}}}],
            "edges": []}


class HandoffTests(unittest.TestCase):
    def test_roundtrip_preserves_notes_and_identity(self):
        original = fixture()
        envelope = export_handoff(original)
        self.assertEqual(import_handoff(envelope, original), original)
        envelope["graph"]["nodes"][0]["notes"]["ui"]["x"] = 5
        self.assertEqual(original["nodes"][0]["notes"]["ui"]["x"], 2)
        envelope["graph"]["revision"] = "r2"
        self.assertEqual(import_handoff(envelope, original)["revision"], "r2")

    def test_reject_stale_revision_and_hash(self):
        original = fixture()
        envelope = export_handoff(original)
        changed = copy.deepcopy(original)
        changed["revision"] = "r2"
        with self.assertRaises(GraphError):
            import_handoff(envelope, changed)
        changed = copy.deepcopy(original)
        changed["nodes"][0]["notes"]["purpose"] = "different"
        with self.assertRaises(GraphError):
            import_handoff(envelope, changed)

    def test_reject_unsupported_fields_and_unversioned_edit(self):
        original = fixture()
        envelope = export_handoff(original)
        envelope["graph"]["nodes"][0]["notes"]["purpose"] = "changed"
        with self.assertRaises(GraphError):
            import_handoff(envelope, original)
        envelope["graph"]["revision"] = "r2"
        envelope["graph"]["nodes"][0]["unknown"] = True
        with self.assertRaises(GraphError):
            import_handoff(envelope, original)

    def test_reject_tampered_envelope(self):
        original = fixture()
        envelope = export_handoff(original)
        envelope["extra"] = "silent loss"
        with self.assertRaises(GraphError):
            import_handoff(envelope, original)


if __name__ == "__main__":
    unittest.main()
