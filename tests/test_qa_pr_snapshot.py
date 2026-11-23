import unittest
from collections import UserDict

from mojum.qa_pr_snapshot import SCHEMA, admit_pr_snapshot


def fixture():
    return {
        "repository": "Garyu-tt/mojum", "number": 42, "state": "open",
        "draft": True, "merged": False, "mergeable": None,
        "base_ref": "main", "base_sha": "a" * 40,
        "head_ref": "feature/test", "head_sha": "b" * 40,
        "created_at": "2026-11-23T10:00:00Z",
        "updated_at": "2026-11-23T10:01:00+00:00",
        "dependencies": [9, 3],
    }


class PRSnapshotTests(unittest.TestCase):
    def test_admits_and_detaches(self):
        raw = fixture()
        result = admit_pr_snapshot(raw)
        raw["dependencies"].append(7)
        self.assertEqual(result.schema, SCHEMA)
        self.assertEqual(result.dependencies, (3, 9))
        self.assertEqual(len(result.digest), 64)

    def test_dependency_order_does_not_change_digest(self):
        a, b = fixture(), fixture()
        b["dependencies"].reverse()
        self.assertEqual(admit_pr_snapshot(a).digest, admit_pr_snapshot(b).digest)

    def test_rejects_bool_as_number(self):
        raw = fixture()
        raw["number"] = True
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_rejects_bool_dependency(self):
        raw = fixture()
        raw["dependencies"] = [True]
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_rejects_private_or_unbounded_payload_fields(self):
        for key in ("body", "patch", "token", "journal", "url"):
            raw = fixture()
            raw[key] = "not accepted"
            with self.subTest(key=key), self.assertRaises(ValueError):
                admit_pr_snapshot(raw)

    def test_rejects_unknown_and_missing_fields(self):
        raw = fixture()
        raw["arbitrary"] = "x"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)
        raw = fixture()
        del raw["head_sha"]
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_rejects_invalid_repository_components(self):
        raw = fixture()
        raw["repository"] = "../mojum"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_rejects_invalid_sha_and_ref(self):
        for key, value in (("head_sha", "z" * 40), ("base_ref", "../main"),\n                          ("base_ref", ".hidden/main"), ("head_ref", "feature/.hidden")):
            raw = fixture()
            raw[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                admit_pr_snapshot(raw)

    def test_rejects_timezone_free_and_reversed_timestamps(self):
        raw = fixture()
        raw["created_at"] = "2026-11-23T10:00:00"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)
        raw = fixture()
        raw["updated_at"] = "2026-11-23T09:59:00Z"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_rejects_duplicate_self_and_oversized_dependencies(self):
        for deps in ([3, 3], [42], list(range(1, 66))):
            raw = fixture()
            raw["dependencies"] = deps
            with self.subTest(deps=len(deps)), self.assertRaises(ValueError):
                admit_pr_snapshot(raw)

    def test_rejects_unknown_states_and_non_native_booleans(self):
        raw = fixture()
        raw["state"] = "pending"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)
        raw = fixture()
        raw["draft"] = 1
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_rejects_non_mapping_and_non_string_keys(self):
        with self.assertRaises(ValueError):
            admit_pr_snapshot([])
        with self.assertRaises(ValueError):
            admit_pr_snapshot(UserDict(fixture()))

        class HostileKey(str):
            calls = 0

            def __hash__(self):
                type(self).calls += 1
                return super().__hash__()

        hostile = fixture()
        key = HostileKey("untrusted")
        hostile[key] = "x"
        calls_after_insertion = HostileKey.calls
        with self.assertRaises(ValueError):
            admit_pr_snapshot(hostile)
        self.assertEqual(HostileKey.calls, calls_after_insertion)

        raw = fixture()
        raw[1] = "x"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

        raw = fixture()
        raw["x" * 33] = "oversized key"
        with self.assertRaisesRegex(ValueError, "bounded exact native strings"):
            admit_pr_snapshot(raw)


if __name__ == "__main__":
    unittest.main()
