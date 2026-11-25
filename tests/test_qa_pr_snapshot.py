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
        for repository in ("../mojum", "owner/.", "owner/..", ".owner/repo"):
            raw = fixture()
            raw["repository"] = repository
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                admit_pr_snapshot(raw)

    def test_admits_dot_prefixed_repository_name(self):
        raw = fixture()
        raw["repository"] = "Example-Org/.github"
        self.assertEqual(admit_pr_snapshot(raw).repository, "example-org/.github")

    def test_rejects_invalid_sha_and_ref(self):
        for key, value in (("head_sha", "z" * 40), ("base_ref", "../main"),
                          ("base_ref", ".hidden/main"), ("head_ref", "feature/.hidden")):
            raw = fixture()
            raw[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                admit_pr_snapshot(raw)

    def test_admits_valid_git_branch_names_without_rewriting_them(self):
        # These are branch names beneath refs/heads, not standalone full refs.
        # A dot may end an intermediate component, and '@' is a valid branch.
        for field in ("base_ref", "head_ref"):
            for value in ("@", "feature/segment./child", "feature/a+b",
                          "feature/user@name", "feature/\u0442\u0435\u0441\u0442"):
                raw = fixture()
                raw[field] = value
                with self.subTest(field=field, value=value):
                    self.assertEqual(getattr(admit_pr_snapshot(raw), field), value)

    def test_rejects_invalid_git_branch_names(self):
        for field in ("base_ref", "head_ref"):
            for value in ("HEAD", "-option", "/start", "end/", "end.", "double//slash",
                          "two..dots", "feature/.hidden", "feature/name.lock/child",
                          "feature/@{name}", "with space", "with\tcontrol",
                          "with\x7fcontrol", "with~tilde", "with^caret", "with:colon",
                          "with?question", "with*star", "with[bracket", "with\\slash",
                          "feature/\ud800", "feature/\udfff"):
                raw = fixture()
                raw[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
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

    def test_rejects_inconsistent_pr_lifecycle_states(self):
        for changes in (
            {"state": "open", "draft": False, "merged": True},
            {"state": "open", "draft": True, "merged": True},
        ):
            raw = fixture()
            raw.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                admit_pr_snapshot(raw)

    def test_preserves_draft_metadata_on_closed_snapshots(self):
        # GitHub retains the draft flag on closed records. Admission records
        # upstream metadata; it is not a ready-to-merge decision.
        for merged in (False, True):
            raw = fixture()
            open_digest = admit_pr_snapshot(raw).digest
            raw.update(state="closed", draft=True, merged=merged, mergeable=False)
            with self.subTest(merged=merged):
                result = admit_pr_snapshot(raw)
                self.assertEqual(result.state, "closed")
                self.assertIs(result.draft, True)
                self.assertIs(result.merged, merged)
                self.assertIs(result.mergeable, False)
                self.assertNotEqual(result.digest, open_digest)

    def test_rejects_unknown_states_and_non_native_booleans(self):
        raw = fixture()
        raw["state"] = "pending"
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)
        raw = fixture()
        raw["draft"] = 1
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

    def test_bounds_state_before_enum_lookup(self):
        raw = fixture()
        raw["state"] = "x" * 1_000_000
        with self.assertRaisesRegex(ValueError, "bounded string"):
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
        value = hostile.pop("mergeable")
        key = HostileKey("mergeable")
        hostile[key] = value
        calls_after_insertion = HostileKey.calls
        with self.assertRaises(ValueError):
            admit_pr_snapshot(hostile)
        self.assertEqual(HostileKey.calls, calls_after_insertion)

        raw = fixture()
        value = raw.pop("mergeable")
        raw[1] = value
        with self.assertRaises(ValueError):
            admit_pr_snapshot(raw)

        raw = fixture()
        value = raw.pop("mergeable")
        raw["x" * 33] = value
        with self.assertRaisesRegex(ValueError, "bounded exact native strings"):
            admit_pr_snapshot(raw)


if __name__ == "__main__":
    unittest.main()
