"""Untrusted, offline PR stack DAG admission and ordering tests."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "tools" / "pr_stack_audit.py"
SPEC = importlib.util.spec_from_file_location("mojum_pr_stack_audit", FILE)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def item(n, deps=(), base=None, state="open", sha=None):
    return {
        "number": n,
        "state": state,
        "head_ref": f"build/pr-{n}",
        "head_sha": sha or f"{n:040x}",
        "base_ref": f"build/pr-{base}" if base else "main",
        "depends_on": list(deps),
        "base_pr": base,
        "parent_head_sha": f"{base:040x}" if base else None,
    }


def snapshot(*prs):
    return {"schema": AUDIT.SCHEMA, "base_branch": "main", "pull_requests": list(prs)}


class PrStackAuditTests(unittest.TestCase):
    def analyze(self, *prs):
        return AUDIT.audit(snapshot(*prs))

    def codes(self, report):
        return {issue["code"] for issue in report["blockers"]}

    def test_linear_and_diamond_topological_order(self):
        a = item(10)
        b = item(20, (10,), 10)
        c = item(30, (10,))
        d = item(40, (20, 30), 20)
        result = self.analyze(d, c, b, a)
        self.assertTrue(result["ok"])
        self.assertEqual(result["topological_order"], [10, 20, 30, 40])
        self.assertEqual(result["dependency_count"], 4)
        self.assertEqual(result["blockers"], [])

    def test_cycle_and_self_dependency(self):
        a = item(11, (12,))
        b = item(12, (11,))
        result = self.analyze(b, a, item(17, (17,)))
        self.assertFalse(result["ok"])
        self.assertIn("self_dependency", self.codes(result))
        self.assertEqual([x["cycle"] for x in result["blockers"] if x["code"] == "dependency_cycle"], [[11, 12, 11], [17, 17]])
        self.assertEqual(result["topological_order"], [])

    def test_missing_closed_and_merged_dependencies(self):
        report = self.analyze(item(7, (9, 10, 11)), item(10, state="closed"), item(11, state="merged"))
        self.assertEqual(self.codes(report), {"missing_dependency", "closed_dependency"})

    def test_mismatched_parent_ref_and_sha(self):
        child = item(5, (4,), 4)
        child["base_ref"] = "build/wrong"
        child["parent_head_sha"] = "e" * 40
        report = self.analyze(item(4), child)
        self.assertEqual(self.codes(report), {"base_ref_mismatch", "stale_parent_head"})

    def test_merged_base_parent_requires_rebase(self):
        report = self.analyze(item(3, state="merged"), item(4, (3,), 3))
        self.assertEqual(self.codes(report), {"inactive_base_parent"})

    def test_undeclared_and_untracked_base(self):
        report = self.analyze(item(1), item(2, base=1), item(3))
        self.assertIn("base_parent_not_declared", self.codes(report))
        rogue = item(3)
        rogue["base_ref"] = "untracked/fork"
        self.assertIn("untracked_base_ref", self.codes(self.analyze(rogue)))

    def test_order_independent_deterministic_report(self):
        prs = [item(3, (2,)), item(2, (42,)), item(4, (2,))]
        data1 = AUDIT.audit(snapshot(*prs))
        data2 = AUDIT.audit(snapshot(*reversed(prs)))
        self.assertEqual(data1, data2)

    def test_reject_duplicate_pr_and_head_ref(self):
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(item(3), item(3))
        duplicate_head = item(5)
        duplicate_head["head_ref"] = "build/pr-3"
        self.assertIn("duplicate_head_ref", self.codes(self.analyze(item(3), duplicate_head)))

    def test_reject_type_confusion_and_invalid_parents(self):
        bad = item(1)
        bad["number"] = True
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(bad)
        bad = item(1)
        bad["depends_on"] = [1.0]
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(bad)
        bad = item(1)
        bad["parent_head_sha"] = "f" * 40
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(bad)
        bad = item(1)
        bad["head_ref"] = "../unsafe"
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(bad)
        for invalid_ref in ("build/./worker", "build/.hidden", "build/worker.", "build/worker.lock"):
            bad = item(1)
            bad["head_ref"] = invalid_ref
            with self.subTest(ref=invalid_ref), self.assertRaises(AUDIT.SnapshotError):
                self.analyze(bad)

    def test_reject_hostile_string_subclass_keys_before_comparison(self):
        class HostileKey(str):
            explode = False
            def __hash__(self):
                if self.explode:
                    raise RuntimeError("hostile hash executed")
                return super().__hash__()

        value = snapshot(item(1))
        original = value.pop("schema")
        key = HostileKey("schema")
        value[key] = original
        key.explode = True
        with self.assertRaises(AUDIT.SnapshotError):
            AUDIT.validate_snapshot(value)

    def test_reject_duplicate_json_fields_and_unknown_keys(self):
        with self.assertRaises(AUDIT.SnapshotError):
            AUDIT.decode_snapshot(b'{"schema":"a","schema":"b"}')
        bad = snapshot(item(1))
        bad["unexpected"] = "ignored?"
        with self.assertRaises(AUDIT.SnapshotError):
            AUDIT.audit(bad)

    def test_byte_and_edge_budgets(self):
        with self.assertRaises(AUDIT.SnapshotError):
            AUDIT.decode_snapshot(b" " * (AUDIT.MAX_INPUT_BYTES + 1))
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(*(item(n) for n in range(1, AUDIT.MAX_PRS + 2)))
        pr = item(1, tuple(range(2, 67)))
        with self.assertRaises(AUDIT.SnapshotError):
            self.analyze(pr)

    def test_prevent_huge_integer_and_non_finite_literals(self):
        raw = b'{"schema":"mojum.pr-stack-snapshot/1","base_branch":"main","pull_requests":[{"number":' + b"9" * 1000 + b'}]}'
        with self.assertRaises(AUDIT.SnapshotError):
            AUDIT.decode_snapshot(raw)
        with self.assertRaises(AUDIT.SnapshotError):
            AUDIT.decode_snapshot(b'{"schema":NaN}')

    def test_cli_exit_codes_and_stable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(snapshot(item(1), item(2, (1,), 1))), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(AUDIT.main([str(path)]), 0)
            self.assertEqual(json.loads(buf.getvalue())["topological_order"], [1, 2])
            path.write_text(json.dumps(snapshot(item(5, (9,)))), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(AUDIT.main([str(path)]), 1)
            path.write_text("INVALID", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(AUDIT.main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
