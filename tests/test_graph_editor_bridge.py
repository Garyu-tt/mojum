"""CPU-only, offline bridge acceptance for integration issue #699."""
import copy
import json
import unittest
from pathlib import Path

from mojum.graph import GraphError
from mojum.graph_editor_bridge import (
    WIRE_SCHEMA, export_editor, import_editor,
)
from mojum.graph_v2 import downgrade_v2, upgrade_v1, validate_v2

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "first_loop.json")
    .read_text(encoding="utf-8")
)


class GraphEditorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.source = upgrade_v1(FIXTURE)

    def _import(self, envelope, **kwargs):
        return import_editor(
            envelope, expected_revision=self.source["revision"],
            expected_sha256=export_editor(
                self.source, envelope["backend"], allow_private=True
            )["source_sha256"],
            allow_private=True, **kwargs,
        )

    def test_private_data_never_exported_or_imported_implicitly(self):
        for backend in ("reactflow", "rete"):
            with self.subTest(backend=backend):
                with self.assertRaisesRegex(GraphError, "allow_private"):
                    export_editor(self.source, backend)
                envelope = export_editor(self.source, backend, allow_private=True)
                with self.assertRaisesRegex(GraphError, "allow_private"):
                    import_editor(
                        envelope, expected_revision=self.source["revision"],
                        expected_sha256=envelope["source_sha256"],
                    )

    def test_public_graph_does_not_need_private_opt_in(self):
        public = upgrade_v1(
            FIXTURE, labels={node["id"]: "PUBLIC" for node in FIXTURE["nodes"]}
        )
        envelope = export_editor(public, "reactflow")
        restored = import_editor(
            envelope, expected_revision=public["revision"],
            expected_sha256=envelope["source_sha256"],
        )
        self.assertEqual(restored["graph"], public)

    def test_exact_roundtrip_preserves_notes_ports_and_revision(self):
        for backend in ("reactflow", "rete"):
            with self.subTest(backend=backend):
                original = copy.deepcopy(self.source)
                envelope = export_editor(self.source, backend, allow_private=True)
                self.assertEqual(envelope["schema"], WIRE_SCHEMA)
                self.assertEqual(len(envelope["nodes"]), 6)
                self.assertEqual(len(envelope["edges"]), 6)
                self.assertEqual(envelope, export_editor(self.source, backend,
                                                         allow_private=True))
                restored = self._import(envelope)
                self.assertEqual(restored["graph"], original)
                self.assertEqual(restored["execution"], "NOT_PERFORMED")
                self.assertFalse(restored["notes_changed"])
                self.assertEqual(self.source, original)

    def test_framework_specific_handles_retain_typed_endpoints(self):
        react = export_editor(self.source, "reactflow", allow_private=True)
        rete = export_editor(self.source, "rete", allow_private=True)
        self.assertEqual(
            (react["edges"][0]["sourceHandle"], react["edges"][0]["targetHandle"]),
            ("genome", "genome"),
        )
        self.assertEqual(
            (rete["edges"][0]["sourceOutput"], rete["edges"][0]["targetInput"]),
            ("genome", "genome"),
        )
        self.assertEqual(rete["nodes"][0]["outputs"]["genome"]["socket"], "genome/1")
        self.assertEqual(react["nodes"][0]["data"]["ports"]["outputs"],
                         {"genome": "genome/1"})

    def test_layout_changes_are_detached_from_canonical(self):
        for backend in ("reactflow", "rete"):
            with self.subTest(backend=backend):
                envelope = export_editor(self.source, backend, allow_private=True)
                envelope["nodes"][0]["position"] = {"x": -999.25, "y": 100000}
                restored = self._import(envelope)
                self.assertEqual(restored["graph"], self.source)
                self.assertEqual(restored["layout"][0]["position"]["x"], -999.25)

    def test_note_edits_generate_new_explicit_candidate_revision(self):
        for backend in ("reactflow", "rete"):
            with self.subTest(backend=backend):
                envelope = export_editor(self.source, backend, allow_private=True)
                envelope["nodes"][1]["data"]["notes"]["purpose"] = "Reviewed offline"
                with self.assertRaisesRegex(GraphError, "new revision"):
                    self._import(envelope)
                result = self._import(envelope, new_revision="notes-r02")
                self.assertTrue(result["notes_changed"])
                self.assertEqual(result["graph"]["revision"], "notes-r02")
                self.assertEqual(result["graph"]["nodes"][1]["notes"]["purpose"],
                                 "Reviewed offline")
                self.assertEqual(result["graph"]["nodes"][0],
                                 self.source["nodes"][0])
                self.assertNotEqual(result["graph"]["origin"],
                                    self.source["origin"])
                validate_v2(result["graph"])
                self.assertEqual(
                    downgrade_v2(result["graph"])["nodes"][1]["notes"]["purpose"],
                    "Reviewed offline",
                )
                self.assertEqual(self.source, upgrade_v1(FIXTURE))

    def test_stale_or_forged_base_rejected(self):
        envelope = export_editor(self.source, "reactflow", allow_private=True)
        with self.assertRaisesRegex(GraphError, "CAS"):
            import_editor(envelope, expected_revision="stale",
                          expected_sha256=envelope["source_sha256"],
                          allow_private=True)
        with self.assertRaisesRegex(GraphError, "CAS"):
            import_editor(envelope, expected_revision=self.source["revision"],
                          expected_sha256="0" * 64, allow_private=True)
        envelope["canonical"]["nodes"][0]["config"]["bpm"] = 90
        with self.assertRaises(GraphError):
            self._import(envelope)

    def test_structural_edits_refuse_instead_of_dropping_data(self):
        for backend in ("reactflow", "rete"):
            for name, mutate in (
                ("edge", lambda w: w["edges"][0].update({"target": "room"})),
                ("port", lambda w: w["nodes"][0]["data"]["ports"]["outputs"].update(
                    {"genome": "unknown/2"})),
                ("id", lambda w: w["nodes"][0].update({"id": "changed"})),
                ("kind", lambda w: w["nodes"][0]["data"].update({"kind": "fake"})),
                ("extra", lambda w: w["nodes"][0]["data"].update({"mystery": 3})),
                ("deleted", lambda w: w["nodes"].pop()),
                ("reordered", lambda w: w["nodes"].reverse()),
                ("nested", lambda w: w["canonical"]["nodes"][0].update(
                    {"container": {"children": []}})),
            ):
                with self.subTest(backend=backend, mutation=name):
                    envelope = export_editor(self.source, backend,
                                             allow_private=True)
                    mutate(envelope)
                    with self.assertRaises(GraphError):
                        self._import(envelope)

    def test_reject_unbounded_and_invalid_positions(self):
        for value in (float("nan"), float("inf"), "1", None, True, 100001, 10 ** 400):
            with self.subTest(value=repr(value)):
                envelope = export_editor(self.source, "reactflow",
                                         allow_private=True)
                envelope["nodes"][0]["position"]["x"] = value
                with self.assertRaises(GraphError):
                    self._import(envelope)

    def test_revision_must_change_if_and_only_if_notes_change(self):
        envelope = export_editor(self.source, "rete", allow_private=True)
        with self.assertRaisesRegex(GraphError, "actual notes edit"):
            self._import(envelope, new_revision="new")
        envelope["nodes"][0]["data"]["notes"]["purpose"] = "changed"
        for value in (None, "", self.source["revision"], " "):
            with self.subTest(revision=value), self.assertRaises(GraphError):
                self._import(envelope, new_revision=value)

    def test_unknown_backend_v1_and_extra_envelope_fields_rejected(self):
        for backend in ("other", [], {}, None):
            with self.subTest(backend=repr(backend)), self.assertRaises(GraphError):
                export_editor(self.source, backend, allow_private=True)
        with self.assertRaises(GraphError):
            export_editor(FIXTURE, "reactflow", allow_private=True)
        envelope = export_editor(self.source, "rete", allow_private=True)
        envelope["unexpected"] = {"must": "not disappear"}
        with self.assertRaises(GraphError):
            self._import(envelope)


if __name__ == "__main__":
    unittest.main()
