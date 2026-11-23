import json
import unittest
from unittest.mock import patch

from mojum.noosphere_validity import (
    SCHEMA,
    ValidityAuditError,
    audit_json,
    audit_validity,
)


def fact(event_id, key="dante.location", start=0, end=10, value="room-a"):
    return {
        "event_id": event_id,
        "fact_key": key,
        "visibility": "PUBLIC",
        "provenance": "PUBLIC_SYNTHETIC",
        "valid_from_ns": start,
        "valid_to_ns": end,
        "value": value,
    }


class NoosphereValidityTests(unittest.TestCase):
    def test_reports_only_conflicting_overlapping_intervals(self):
        report = audit_validity([
            fact("a", start=0, end=10, value={"room": "a"}),
            fact("b", start=5, end=15, value={"room": "b"}),
            fact("c", start=10, end=20, value={"room": "c"}),
            fact("d", key="dante.mood", start=0, end=20, value="calm"),
        ])
        self.assertEqual(report["conflict_count"], 2)
        conflict = report["conflicts"][0]
        self.assertEqual((conflict["left_event_id"], conflict["right_event_id"]), ("a", "b"))
        self.assertEqual((conflict["overlap_from_ns"], conflict["overlap_to_ns"]), (5, 10))
        self.assertNotIn("room", repr(report))
        self.assertEqual(report["scope"], "PUBLIC_SYNTHETIC_ONLY")
        self.assertEqual(report["resolution"], "NOT_PERFORMED")

    def test_same_value_overlap_is_not_conflict(self):
        report = audit_validity([
            fact("a", value={"mood": "calm"}),
            fact("b", start=5, end=None, value={"mood": "calm"}),
        ])
        self.assertEqual(report["conflict_count"], 0)

    def test_open_ended_interval_conflicts(self):
        report = audit_validity([
            fact("a", start=4, end=None, value="one"),
            fact("b", start=9, end=12, value="two"),
        ])
        self.assertEqual(report["conflicts"][0]["overlap_to_ns"], 12)

    def test_input_order_does_not_change_report(self):
        rows = [fact("a", start=0, end=10, value=1), fact("b", start=2, end=9, value=2)]
        self.assertEqual(audit_validity(rows), audit_validity(list(reversed(rows))))

    def test_report_digest_is_stable_and_64_hex(self):
        report = audit_validity([fact("a")])
        self.assertEqual(len(report["sha256"]), 64)
        int(report["sha256"], 16)
        self.assertEqual(report, audit_validity([fact("a")]))

    def test_rejects_non_public_or_unasserted_provenance(self):
        for field, value in (("visibility", "PRIVATE"), ("visibility", "RESTRICTED"),
                             ("provenance", "USER_SUPPLIED")):
            item = fact("a")
            item[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValidityAuditError):
                audit_validity([item])

    def test_rejects_exact_schema_and_type_violations(self):
        bad = []
        item = fact("a"); item["extra"] = True; bad.append(item)
        item = fact("a"); item["valid_from_ns"] = True; bad.append(item)
        item = fact("a"); item["valid_to_ns"] = 0; bad.append(item)
        item = fact("a"); item["event_id"] = ""; bad.append(item)
        item = fact("a"); item["fact_key"] = "x" * 257; bad.append(item)
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValidityAuditError):
                audit_validity([item])

    def test_rejects_duplicate_ids_and_oversized_fact_collections(self):
        with self.assertRaises(ValidityAuditError):
            audit_validity([fact("same"), fact("same", key="other")])
        with self.assertRaises(ValidityAuditError):
            audit_validity([fact(str(i)) for i in range(4097)])

    def test_rejects_non_json_values_and_hostile_shapes(self):
        class HostileString(str):
            def __hash__(self):
                return hash("visibility")

            def __eq__(self, other):
                raise RuntimeError("hostile equality invoked")

        malformed = [
            {HostileString("visibility"): "PUBLIC"},
            {"not": object()},
            {"cycle": []},
            float("nan"),
            2**80,
            "\ud800",
        ]
        malformed[2]["cycle"].append(malformed[2])
        for value in malformed:
            item = fact("a", value=value)
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValidityAuditError):
                audit_validity([item])

    def test_rejects_shared_container_aliases(self):
        shared = [1]
        with self.assertRaises(ValidityAuditError):
            audit_validity([fact("a", value=[shared, shared])])

    def test_wire_parser_rejects_duplicate_keys_and_bad_json(self):
        valid = {"schema": SCHEMA, "facts": [fact("a")]}
        self.assertEqual(audit_json(json.dumps(valid))["conflict_count"], 0)
        duplicate = '{"schema":"%s","schema":"%s","facts":[]}' % (SCHEMA, SCHEMA)
        bad_values = [duplicate, '{"schema":"%s","facts":[{"x":NaN}]}' % SCHEMA,
                      b"\xff", "\ud800", "{" * 300]
        for wire in bad_values:
            with self.subTest(wire_type=type(wire).__name__), self.assertRaises(ValidityAuditError):
                audit_json(wire)

    def test_wire_parser_rejects_wrong_envelope_and_large_bytes(self):
        with self.assertRaises(ValidityAuditError):
            audit_json(json.dumps({"schema": "wrong", "facts": []}))
        with self.assertRaises(ValidityAuditError):
            audit_json(b" " * (8 * 1024 * 1024 + 1))

    def test_value_fanout_is_rejected_before_stack_or_entry_materialization(self):
        oversized_list = list(range(100_001))
        oversized_dict = {f"key-{index}": index for index in range(100_001)}

        for value in (oversized_list, oversized_dict):
            with self.subTest(value_type=type(value).__name__):
                with patch("mojum.noosphere_validity.json.dumps") as dumps:
                    with self.assertRaisesRegex(ValidityAuditError, "node budget"):
                        audit_validity([fact("oversized", value=value)])
                    dumps.assert_not_called()

    def test_budgets_bound_pathological_overlap(self):
        rows = [fact(str(i), start=0, end=None, value="same") for i in range(800)]
        with self.assertRaisesRegex(ValidityAuditError, "comparison budget"):
            audit_validity(rows)


if __name__ == "__main__":
    unittest.main()
