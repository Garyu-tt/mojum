from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from mojum.school_transfer_audit import (
    REPORT_SCHEMA, SchoolTransferAuditError, audit_catalog,
)

CATALOG = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class SchoolTransferAuditTests(unittest.TestCase):
    def setUp(self):
        self.raw = CATALOG.read_bytes()
        self.catalog = json.loads(self.raw)

    def test_current_catalog_is_auditable_and_report_is_redacted(self):
        report = audit_catalog(self.raw)
        data = report.as_dict()
        self.assertEqual(REPORT_SCHEMA, data["schema"])
        self.assertGreater(data["section_count"], 0)
        self.assertGreater(data["cross_link_count"], 0)
        self.assertNotIn("title", data)
        self.assertNotIn("prompt", data)
        self.assertTrue(all(item["code"] for item in data["findings"]))

    def test_mapping_and_bytes_have_same_digest(self):
        self.assertEqual(audit_catalog(self.raw).report_sha256,
                         audit_catalog(self.catalog).report_sha256)

    def test_result_is_deterministic_and_sorted(self):
        first = audit_catalog(self.catalog).as_dict()
        second = audit_catalog(copy.deepcopy(self.catalog)).as_dict()
        self.assertEqual(first, second)
        keys = [(x["code"], x["source"], x["target"], x["detail"])
                for x in first["findings"]]
        self.assertEqual(keys, sorted(keys))

    def test_input_is_not_mutated(self):
        before = copy.deepcopy(self.catalog)
        audit_catalog(self.catalog)
        self.assertEqual(before, self.catalog)

    def test_reports_asymmetric_subject_links_without_rejecting_them(self):
        catalog = {
            "schema": "mojum.school-catalog/1",
            "sections": [
                {"id": "alpha", "cross_subject_ids": ["beta"]},
                {"id": "beta", "cross_subject_ids": []},
            ],
        }
        findings = audit_catalog(catalog).findings
        self.assertIn("MISSING_REVERSE_CROSS_SUBJECT_LINK",
                      [item.code for item in findings])

    def test_unknown_and_self_references_are_reported(self):
        catalog = {
            "schema": "mojum.school-catalog/1",
            "sections": [
                {"id": "alpha", "cross_subject_ids": ["alpha", "missing"],
                 "lessons": [{"id": "alpha.lesson", "transfer_to": ["alpha", "missing"]}]},
            ],
        }
        codes = {item.code for item in audit_catalog(catalog).findings}
        self.assertIn("SELF_CROSS_SUBJECT_LINK", codes)
        self.assertIn("UNKNOWN_CROSS_SUBJECT_TARGET", codes)
        self.assertIn("SELF_LESSON_TRANSFER", codes)
        self.assertIn("UNKNOWN_LESSON_TRANSFER_TARGET", codes)

    def test_duplicate_links_and_lesson_ids_are_reported(self):
        catalog = {
            "schema": "mojum.school-catalog/1",
            "sections": [
                {"id": "alpha", "cross_subject_ids": ["beta", "beta"],
                 "lessons": [{"id": "shared.lesson", "transfer_to": ["beta", "beta"]}]},
                {"id": "beta", "cross_subject_ids": ["alpha"],
                 "lessons": [{"id": "shared.lesson", "transfer_to": []}]},
            ],
        }
        codes = [item.code for item in audit_catalog(catalog).findings]
        self.assertIn("DUPLICATE_CROSS_SUBJECT_LINK", codes)
        self.assertIn("DUPLICATE_LESSON_TRANSFER", codes)
        self.assertIn("DUPLICATE_LESSON_ID", codes)

    def test_duplicate_json_keys_are_rejected(self):
        raw = b'{"schema":"mojum.school-catalog/1","schema":"other","sections":[]}'
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(raw)

    def test_unknown_schema_is_rejected(self):
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog({"schema": "mojum.school-catalog/2", "sections": []})

    def test_non_native_containers_and_keys_are_rejected(self):
        class OddDict(dict):
            pass

        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(OddDict(schema="mojum.school-catalog/1", sections=[]))
        bad = {"schema": "mojum.school-catalog/1", "sections": []}
        bad[1] = "not a JSON key"
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(bad)

    def test_non_finite_and_non_json_values_are_rejected(self):
        bad = {"schema": "mojum.school-catalog/1", "sections": [], "x": float("nan")}
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(bad)
        bad["x"] = object()
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(bad)

    def test_cycles_and_deep_trees_are_rejected(self):
        cycle = {"schema": "mojum.school-catalog/1", "sections": []}
        cycle["cycle"] = cycle
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(cycle)
        deep = current = {"schema": "mojum.school-catalog/1", "sections": []}
        for _ in range(30):
            current["nested"] = {}
            current = current["nested"]
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(deep)

    def test_catalog_and_link_budgets_are_enforced(self):
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(b" " * 65_537)
        catalog = {
            "schema": "mojum.school-catalog/1",
            "sections": [{"id": "alpha", "cross_subject_ids": ["x"] * 129}],
        }
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog(catalog)

    def test_duplicate_section_and_invalid_lesson_list_fail_closed(self):
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog({"schema": "mojum.school-catalog/1", "sections": [
                {"id": "alpha"}, {"id": "alpha"},
            ]})
        with self.assertRaises(SchoolTransferAuditError):
            audit_catalog({"schema": "mojum.school-catalog/1", "sections": [
                {"id": "alpha", "lessons": {}},
            ]})


if __name__ == "__main__":
    unittest.main()
