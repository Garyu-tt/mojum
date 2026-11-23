"""Adversarial stdlib tests for offline changed-file collision admission."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "pr_file_collision_audit.py"
spec = importlib.util.spec_from_file_location("pr_file_collision_audit", SCRIPT)
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def pr(number: int, changed: list[str], allowed: list[str] | None = None) -> dict:
    return {
        "number": number,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "state": "open",
        "draft": True,
        "changed_paths": changed,
        "allowed_paths": changed[:] if allowed is None else allowed,
    }


def inventory(*prs: dict) -> dict:
    return {"schema": audit.SCHEMA, "prs": list(prs)}


def encoded(item: object) -> bytes:
    return json.dumps(item, separators=(",", ":")).encode("utf-8")


class AuditTests(unittest.TestCase):
    def test_disjoint_paths_clear(self):
        result = audit.audit_manifest(inventory(
            pr(18, ["src/mojum/x.py"]), pr(19, ["docs/y.md"])))
        self.assertTrue(result["clear"])
        self.assertEqual(result["checked_prs"], 2)
        self.assertEqual(result["checked_changed_paths"], 2)
        self.assertEqual(result["same_path_collisions"], [])
        self.assertEqual(len(result["snapshot_sha256"]), 64)

    def test_snapshot_is_order_independent(self):
        a = inventory(pr(21, ["docs/b.md", "docs/a.md"]),
                      pr(20, ["tests/c.py"]))
        b = inventory(pr(20, ["tests/c.py"]),
                      pr(21, ["docs/a.md", "docs/b.md"]))
        self.assertEqual(audit.audit_manifest(a), audit.audit_manifest(b))

    def test_does_not_mutate_caller_records(self):
        value = inventory(pr(11, ["docs/z.md", "docs/a.md"]))
        before = copy.deepcopy(value)
        audit.audit_manifest(value)
        self.assertEqual(value, before)

    def test_same_file_collision_stably_sorted(self):
        result = audit.audit_manifest(inventory(
            pr(30, ["docs/shared.md", "docs/other.md"]),
            pr(10, ["docs/shared.md"]),
            pr(20, ["docs/shared.md"]),
        ))
        self.assertFalse(result["clear"])
        self.assertEqual(result["same_path_collisions"], [{
            "path": "docs/shared.md", "prs": [10, 20, 30]}])

    def test_file_directory_collision(self):
        result = audit.audit_manifest(inventory(
            pr(10, ["docs/item"]), pr(11, ["docs/item/sub.md"])))
        self.assertFalse(result["clear"])
        self.assertEqual(result["file_directory_collisions"], [{
            "file_path": "docs/item",
            "descendant_path": "docs/item/sub.md",
            "prs": [10, 11],
        }])

    def test_file_directory_inside_same_pr_is_invalid_tree_finding(self):
        result = audit.audit_manifest(inventory(
            pr(10, ["docs/item", "docs/item/sub.md"])))
        self.assertFalse(result["clear"])
        self.assertEqual(len(result["file_directory_collisions"]), 1)

    def test_ungranted_changed_file(self):
        result = audit.audit_manifest(inventory(
            pr(10, ["src/mojum/x.py", "tests/x.py"], ["src/mojum/x.py"])))
        self.assertFalse(result["clear"])
        self.assertEqual(result["out_of_scope"], [
            {"pr": 10, "paths": ["tests/x.py"]}])

    def test_allowed_root_and_github_files(self):
        result = audit.audit_manifest(inventory(pr(
            1, [".github/workflows/test.yml", "pyproject.toml"])))
        self.assertTrue(result["clear"])

    def test_all_findings_stable_through_json(self):
        value = inventory(
            pr(1, ["tools/locked.py", "docs/x"], ["docs/x"]),
            pr(2, ["tools/locked.py", "docs/x/readme.md"]),
        )
        self.assertEqual(audit.audit_json(encoded(value)),
                         audit.audit_manifest(value))

    def test_missing_extra_fields_rejected(self):
        value = pr(1, ["docs/x"])
        del value["base_sha"]
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))
        value = pr(1, ["docs/x"])
        value["secret"] = "unexpected"
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))

    def test_forged_identity_types_rejected(self):
        value = pr(1, ["docs/x"])
        value["number"] = True
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))
        value = pr(1, ["docs/x"])
        value["draft"] = 1
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))
        value = pr(1, ["docs/x"])
        value["state"] = 1
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))

    def test_untrusted_string_subclasses_rejected(self):
        class Poison(str):
            def __eq__(self, other):
                raise RuntimeError("hostile comparison")
        value = pr(1, ["docs/x"])
        value["state"] = Poison("open")
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))
        value = pr(1, [Poison("docs/x")])
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))

    def test_unsafe_paths(self):
        bad = ["../out", "/etc/passwd", "docs//x", "docs/./x",
               "docs/../x", "docs\\x", "docs/x y", ".git/config",
               "docs/x*", "docs/x\x00", "", "z" * 181]
        for path in bad:
            with self.subTest(path=repr(path)):
                with self.assertRaises(audit.CollisionAuditError):
                    audit.audit_manifest(inventory(pr(1, [path])))

    def test_duplicate_pr_and_paths(self):
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(pr(1, ["docs/a"]),
                                           pr(1, ["docs/b"])))
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(pr(1, ["docs/a", "docs/a"])))

    def test_bad_sha(self):
        for sha in ["abc", "A" * 40, "f" * 64, 123]:
            with self.subTest(sha=sha):
                value = pr(1, ["docs/a"])
                value["head_sha"] = sha
                with self.assertRaises(audit.CollisionAuditError):
                    audit.audit_manifest(inventory(value))

    def test_state_or_schema_wrong(self):
        value = pr(1, ["docs/a"])
        value["state"] = "closed"
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(value))
        value = inventory(pr(1, ["docs/a"]))
        value["schema"] = "unsupported/1"
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(value)

    def test_manifest_budget(self):
        for value in [inventory(), inventory(*[
            pr(i + 1, [f"docs/{i}"]) for i in range(audit.MAX_PRS + 1)
        ])]:
            with self.assertRaises(audit.CollisionAuditError):
                audit.audit_manifest(value)
        too_many = [f"docs/{i}" for i in range(audit.MAX_FILES_PER_PR + 1)]
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_manifest(inventory(pr(1, too_many)))

    def test_input_bytes_budget_and_invalid_utf8(self):
        for raw in [b"", b"x" * (audit.MAX_INPUT_BYTES + 1),
                    b"\xff", b"{", b"[]"]:
            with self.subTest(size=len(raw)):
                with self.assertRaises(audit.CollisionAuditError):
                    audit.audit_json(raw)

    def test_duplicate_json_keys_fail_closed(self):
        raw = b'{"schema":"mojum.pr-file-collision-input/1","prs":[],"prs":[]}'
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_json(raw)

    def test_nonfinite_or_float_or_giant_number_rejected(self):
        for raw in [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1.0}',
                    b'{"x":123456789012345}']:
            with self.subTest(raw=raw):
                with self.assertRaises(audit.CollisionAuditError):
                    audit.audit_json(raw)

    def test_recursive_json_rejected(self):
        with self.assertRaises(audit.CollisionAuditError):
            audit.audit_json(b"[" * 1500 + b"0" + b"]" * 1500)

    def test_cli_status_codes(self):
        good = encoded(inventory(pr(1, ["docs/x"])))
        conflict = encoded(inventory(
            pr(1, ["docs/x"]), pr(2, ["docs/x"])))
        for raw, code in [(good, 0), (conflict, 1), (b"{}", 2)]:
            with self.subTest(exit=code):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT)], input=raw,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=False, timeout=10)
                self.assertEqual(proc.returncode, code, proc.stderr)
                if code < 2:
                    self.assertEqual(json.loads(proc.stdout)["clear"], code == 0)
                else:
                    self.assertFalse(proc.stdout)


if __name__ == "__main__":
    unittest.main()
