import hashlib
import json
import unittest

from mojum.qa_audit_chain import (
    AuditChainError, GENESIS, SCHEMA, record_digest, verify_audit_chain,
)


def make_chain(rows):
    result = []
    previous = GENESIS
    for seq, (kind, state, reason, at_ms) in enumerate(rows, 1):
        item = {
            "seq": seq, "kind": kind, "task_key": "pr:123",
            "state": state, "reason": reason, "at_ms": at_ms,
            "previous_digest": previous,
        }
        item["digest"] = record_digest(item)
        previous = item["digest"]
        result.append(item)
    return result


class AuditChainTests(unittest.TestCase):
    def test_empty_chain(self):
        report = verify_audit_chain([])
        self.assertTrue(report["verified"])
        self.assertEqual(report["record_count"], 0)
        self.assertEqual(report["head_digest"], GENESIS)

    def test_valid_ordered_chain(self):
        chain = make_chain([
            ("enqueued", "queued", None, 100),
            ("claimed", "claimed", None, 110),
            ("completed", "completed", None, 120),
        ])
        report = verify_audit_chain(chain)
        self.assertEqual(report["schema"], SCHEMA)
        self.assertEqual(report["last_seq"], 3)

    def test_mapping_insertion_order_does_not_change_digest(self):
        row = make_chain([("enqueued", "queued", None, 1)])[0]
        reversed_row = dict(reversed(list(row.items())))
        self.assertEqual(record_digest(row), record_digest(reversed_row))

    def test_rejects_mutated_payload(self):
        chain = make_chain([("enqueued", "queued", None, 1)])
        chain[0]["state"] = "dead"
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_broken_link(self):
        chain = make_chain([
            ("enqueued", "queued", None, 1),
            ("claimed", "claimed", None, 2),
        ])
        chain[1]["previous_digest"] = "f" * 64
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_sequence_gap(self):
        chain = make_chain([("enqueued", "queued", None, 1)])
        chain[0]["seq"] = 2
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_boolean_integer(self):
        chain = make_chain([("enqueued", "queued", None, 1)])
        chain[0]["at_ms"] = True
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_time_regression(self):
        chain = make_chain([
            ("enqueued", "queued", None, 2),
            ("claimed", "claimed", None, 1),
        ])
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_unknown_field(self):
        chain = make_chain([("enqueued", "queued", None, 1)])
        chain[0]["payload"] = "secret"
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_unbounded_records(self):
        with self.assertRaises(AuditChainError):
            verify_audit_chain([], max_records=4097)
        with self.assertRaises(AuditChainError):
            verify_audit_chain([{}], max_records=1)

    def test_rejects_malformed_task_key(self):
        chain = make_chain([("enqueued", "queued", None, 1)])
        chain[0]["task_key"] = "../secret"
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)

    def test_rejects_unsupported_enum(self):
        chain = make_chain([("enqueued", "queued", None, 1)])
        chain[0]["kind"] = "shell_command"
        with self.assertRaises(AuditChainError):
            verify_audit_chain(chain)


if __name__ == "__main__":
    unittest.main()
