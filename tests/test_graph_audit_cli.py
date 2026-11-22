"""Independent offline graph audit CLI boundaries; no simulated execution."""

from contextlib import redirect_stdout, redirect_stderr
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from mojum.graph import MAX_JSON_DEPTH, SCHEMA as V1_SCHEMA, validate as validate_v1
from mojum.graph_audit_cli import MAX_INPUT_BYTES, REPORT_SCHEMA, main
from mojum.graph_v2 import V2_SCHEMA, upgrade_v1, validate_v2

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "examples" / "first_loop.json").read_text(encoding="utf-8"))


class GraphAuditCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "graph.json"
        self.source.write_text(json.dumps(FIXTURE), encoding="utf-8")
        self.target = self.root / "new-v2.json"

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = main(list(args))
        self.assertEqual(result, 0)
        self.assertEqual(err.getvalue(), "")
        return json.loads(out.getvalue())

    def rejects(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as result:
                main(list(args))
        self.assertEqual(result.exception.code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("graph audit rejected", err.getvalue())

    def test_audit_v1_reports_only_structure_and_never_executes(self):
        report = self.invoke("audit", "--input", str(self.source))
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["input_schema"], FIXTURE["schema"])
        self.assertEqual(report["node_count"], 6)
        self.assertEqual(report["edge_count"], 6)
        self.assertEqual(report["execution"], "NOT_PERFORMED")
        self.assertNotIn("nodes", report)
        self.assertNotIn("edges", report)

    def test_upgrade_creates_private_no_clobber_exact_v2_with_origin(self):
        prior = self.source.read_bytes()
        report = self.invoke("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertEqual(self.source.read_bytes(), prior)
        self.assertEqual(report["output_schema"], V2_SCHEMA)
        upgraded = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(upgraded, upgrade_v1(FIXTURE))
        self.assertEqual(report["output_sha256"], validate_v2(upgraded)["graph_sha256"])
        if os.name == "posix":
            self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), upgraded)
        self.assertEqual(list(self.root.glob(".mojum-graph-v2-*")), [])

    def test_audit_existing_v2_does_not_write_or_execute(self):
        v2 = upgrade_v1(FIXTURE)
        self.source.write_text(json.dumps(v2), encoding="utf-8")
        report = self.invoke("audit", "--input", str(self.source))
        self.assertEqual(report["input_schema"], V2_SCHEMA)
        self.assertEqual(report["input_sha256"], validate_v2(v2)["graph_sha256"])
        self.assertFalse(self.target.exists())
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertFalse(self.target.exists())

    def test_labels_enforce_direction_and_refuse_bad_migration_before_output(self):
        labels = self.root / "labels.json"
        labels.write_text(json.dumps({"genome": "PUBLIC", "heart": "PRIVATE"}), encoding="utf-8")
        self.invoke("upgrade", "--input", str(self.source), "--output", str(self.target), "--labels", str(labels))
        upgraded = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(next(n for n in upgraded["nodes"] if n["id"] == "heart")["visibility"], "PRIVATE")
        self.target.unlink()
        labels.write_text(json.dumps({"genome": "RESTRICTED", "heart": "PUBLIC"}), encoding="utf-8")
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target), "--labels", str(labels))
        self.assertFalse(self.target.exists())

    def test_invalid_v1_or_mixed_schema_never_creates_output(self):
        bad = copy.deepcopy(FIXTURE)
        bad["nodes"][0]["visibility"] = "PUBLIC"
        self.source.write_text(json.dumps(bad), encoding="utf-8")
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertFalse(self.target.exists())

    def test_duplicate_json_keys_rejected_before_any_graph_validation(self):
        self.source.write_bytes(b'{"schema":"mechanicus.universal-graph/1","schema":"mechanicus.universal-graph/1"}')
        self.rejects("audit", "--input", str(self.source))
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertFalse(self.target.exists())

    def test_malformed_utf8_nonfinite_and_oversize_rejected(self):
        for raw in (b"\xff", b'{"schema":NaN}', b" " * (MAX_INPUT_BYTES + 1)):
            with self.subTest(kind=raw[:16]):
                self.source.write_bytes(raw)
                self.rejects("audit", "--input", str(self.source))
                self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
                self.assertFalse(self.target.exists())

    def test_deeply_nested_json_rejected_before_recursive_decoder(self):
        deep = ("[" * (MAX_JSON_DEPTH + 1)) + ("]" * (MAX_JSON_DEPTH + 1))
        self.source.write_text(deep, encoding="utf-8")
        self.rejects("audit", "--input", str(self.source))
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertFalse(self.target.exists())

    def test_duplicate_or_invalid_labels_fail_closed(self):
        labels = self.root / "labels.json"
        for raw in (b'{"genome":"PUBLIC","genome":"PRIVATE"}', b"[]", b'{"foreign":"PUBLIC"}'):
            with self.subTest(raw=raw):
                labels.write_bytes(raw)
                self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target), "--labels", str(labels))
                self.assertFalse(self.target.exists())

    def test_existing_symlink_cannot_be_replaced(self):
        victim = self.root / "victim.txt"
        victim.write_text("do not edit", encoding="utf-8")
        try:
            self.target.symlink_to(victim)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        self.rejects("upgrade", "--input", str(self.source), "--output", str(self.target))
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not edit")
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(list(self.root.glob(".mojum-graph-v2-*")), [])

    def test_downgrade_creates_private_no_clobber_exact_v1_matching_origin(self):
        v2 = upgrade_v1(FIXTURE)
        checked = validate_v2(v2)
        v2_source = self.root / "graph-v2.json"
        v2_source.write_text(json.dumps(v2), encoding="utf-8")
        v1_target = self.root / "restored-v1.json"
        report = self.invoke("downgrade", "--input", str(v2_source), "--output", str(v1_target))
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["input_schema"], V2_SCHEMA)
        self.assertEqual(report["output_schema"], V1_SCHEMA)
        self.assertEqual(report["input_sha256"], checked["graph_sha256"])
        self.assertEqual(report["output_sha256"], checked["origin_sha256"])
        self.assertEqual(report["node_count"], 6)
        self.assertEqual(report["edge_count"], 6)
        self.assertEqual(report["execution"], "NOT_PERFORMED")
        restored = json.loads(v1_target.read_text(encoding="utf-8"))
        self.assertEqual(restored, FIXTURE)
        validate_v1(restored)
        if os.name == "posix":
            self.assertEqual(v1_target.stat().st_mode & 0o777, 0o600)
        self.rejects("downgrade", "--input", str(v2_source), "--output", str(v1_target))
        self.assertEqual(json.loads(v1_target.read_text(encoding="utf-8")), FIXTURE)
        self.assertEqual(list(self.root.glob(".mojum-graph-v2-*")), [])

    def test_downgrade_rejects_v1_tampered_v2_and_visibility_downgrade(self):
        v1_target = self.root / "restored-v1.json"
        self.rejects("downgrade", "--input", str(self.source), "--output", str(v1_target))
        self.assertFalse(v1_target.exists())

        tampered = upgrade_v1(FIXTURE)
        tampered["nodes"][0]["config"]["bpm"] = 90
        tampered_path = self.root / "tampered-v2.json"
        tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
        self.rejects("downgrade", "--input", str(tampered_path), "--output", str(v1_target))
        self.assertFalse(v1_target.exists())

        forged = upgrade_v1(FIXTURE)
        next(n for n in forged["nodes"] if n["id"] == "genome")["visibility"] = "RESTRICTED"
        next(n for n in forged["nodes"] if n["id"] == "heart")["visibility"] = "PUBLIC"
        forged_path = self.root / "forged-v2.json"
        forged_path.write_text(json.dumps(forged), encoding="utf-8")
        self.rejects("downgrade", "--input", str(forged_path), "--output", str(v1_target))
        self.assertFalse(v1_target.exists())

    def test_downgrade_existing_symlink_cannot_be_replaced(self):
        v2_source = self.root / "graph-v2.json"
        v2_source.write_text(json.dumps(upgrade_v1(FIXTURE)), encoding="utf-8")
        victim = self.root / "victim.txt"
        victim.write_text("do not edit", encoding="utf-8")
        v1_target = self.root / "restored-v1.json"
        try:
            v1_target.symlink_to(victim)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this platform")
        self.rejects("downgrade", "--input", str(v2_source), "--output", str(v1_target))
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not edit")
        self.assertTrue(v1_target.is_symlink())
        self.assertEqual(list(self.root.glob(".mojum-graph-v2-*")), [])


if __name__ == "__main__":
    unittest.main()
