"""Offline hermetic boundary tests for GitHub Projects v2 board discovery."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from mojum.project_v2_board import BoardError, main, reconcile


def item(kind="Issue", number=23, iid="PVTI_23", repo="Garyu-tt/mojum", status="opt-dev"):
    plural = "issues" if kind == "Issue" else "pull"
    return {
        "id": iid,
        "content": {"__typename": kind, "number": number,
                    "url": f"https://github.com/{repo}/{plural}/{number}",
                    "repository": {"nameWithOwner": repo}},
        "fieldValues": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"field": {"id": "PVTSSF_status"}, "optionId": status}
        ]},
    }


def page(nodes=None, *, more=False, cursor=None):
    return {"data": {"user": {"projectV2": {
        "id": "PVT_project", "number": 1, "title": "Mojum board",
        "url": "https://github.com/users/Garyu-tt/projects/1",
        "fields": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"id": "PVTSSF_status", "name": "Status", "options": [
                {"id": "opt-backlog", "name": "Backlog"},
                {"id": "opt-dev", "name": "In Progress"},
                {"id": "opt-qa", "name": "Review"},
            ]}
        ]},
        "items": {"pageInfo": {"hasNextPage": more, "endCursor": cursor},
                  "nodes": [] if nodes is None else nodes},
    }}}}


class ProjectBoardAuditTests(unittest.TestCase):
    def test_discover_real_status_ids_without_inventing_done(self):
        report = reconcile([page([item()])])
        self.assertEqual(report["status_options"][1], {"id": "opt-dev", "name": "In Progress"})
        self.assertEqual(report["target_items"][0]["status_option_id"], "opt-dev")
        self.assertEqual(report["mode"], "read_only")
        self.assertNotIn("Done", [o["name"] for o in report["status_options"]])

    def test_backfill_is_preview_only_and_never_changes_status(self):
        candidates = [
            {"kind": "issue", "number": 23, "url": "https://github.com/Garyu-tt/mojum/issues/23"},
            {"kind": "pr", "number": 500, "url": "https://github.com/Garyu-tt/mojum/pull/500"},
        ]
        source = page([item()])
        original = deepcopy(source)
        report = reconcile([source], candidates)
        self.assertEqual(report["missing_target_cards"], [candidates[1]])
        self.assertEqual(report["action"], "preview_only")
        self.assertEqual(source, original)

    def test_non_target_items_are_not_exposed(self):
        outside = item(repo="elsewhere/private", iid="foreign", number=777)
        report = reconcile([page([outside, item()])])
        self.assertEqual(report["ignored_non_target_items"], 1)
        self.assertNotIn("elsewhere", json.dumps(report))
        self.assertNotIn("777", json.dumps(report))

    def test_duplicate_cards_require_manual_review(self):
        second = item(iid="PVTI_dup", status="opt-qa")
        report = reconcile([page([item(), second])])
        self.assertEqual(report["duplicate_target_cards"], [{"kind": "issue", "number": 23, "count": 2}])
        self.assertEqual(report["action"], "manual_review_required")

    def test_multiple_paginated_pages(self):
        pages = [page([item()], more=True, cursor="cursor1"),
                 page([item("PullRequest", 42, "PVTI_42", status="opt-qa")])]
        self.assertEqual(len(reconcile(pages)["target_items"]), 2)
        with self.assertRaisesRegex(BoardError, "incomplete"):
            reconcile(pages[:1])

    def test_repeated_item_id_and_cursor_fail_closed(self):
        with self.assertRaisesRegex(BoardError, "repeated project item ID"):
            reconcile([page([item(), item()])])
        pages = [page([], more=True, cursor="same"),
                 page([], more=True, cursor="same"), page([])]
        with self.assertRaisesRegex(BoardError, "repeated page cursor"):
            reconcile(pages)

    def test_status_field_option_and_card_forgery_rejected(self):
        wrong = page([item(status="unknown")])
        with self.assertRaisesRegex(BoardError, "unknown Status"):
            reconcile([wrong])
        wrong = page()
        options = wrong["data"]["user"]["projectV2"]["fields"]["nodes"][0]["options"]
        options.append({"id": "different", "name": "backlog"})
        with self.assertRaisesRegex(BoardError, "duplicate Status"):
            reconcile([wrong])
        wrong = page()
        wrong["data"]["user"]["projectV2"]["fields"]["nodes"] = []
        with self.assertRaisesRegex(BoardError, "exactly one"):
            reconcile([wrong])

    def test_wrong_board_and_missing_field_pagination_rejected(self):
        wrong = page()
        wrong["data"]["user"]["projectV2"]["url"] = "https://github.com/users/other/projects/1"
        with self.assertRaisesRegex(BoardError, "wrong project"):
            reconcile([wrong])
        wrong = page([item()])
        wrong["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["pageInfo"]["hasNextPage"] = True
        with self.assertRaisesRegex(BoardError, "incomplete field values"):
            reconcile([wrong])

    def test_candidate_mismatch_and_duplicate_rejected(self):
        candidate = {"kind": "issue", "number": 1, "url": "https://github.com/other/repo/issues/1"}
        with self.assertRaisesRegex(BoardError, "outside target"):
            reconcile([page()], [candidate])
        valid = {"kind": "issue", "number": 1, "url": "https://github.com/Garyu-tt/mojum/issues/1"}
        with self.assertRaisesRegex(BoardError, "duplicate input"):
            reconcile([page()], [valid, valid])

    def test_no_truthy_bool_int_or_noncanonical_target_url(self):
        wrong = page([item()])
        wrong["data"]["user"]["projectV2"]["items"]["nodes"][0]["content"]["number"] = True
        with self.assertRaisesRegex(BoardError, "positive integer"):
            reconcile([wrong])
        wrong = page([item()])
        wrong["data"]["user"]["projectV2"]["items"]["nodes"][0]["content"]["url"] += "?secret=1"
        with self.assertRaisesRegex(BoardError, "URL/repository mismatch"):
            reconcile([wrong])

    def test_offline_cli_and_duplicate_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.json"
            path.write_text(json.dumps(page([item()])), encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["--snapshot", str(path)]), 0)
            self.assertEqual(json.loads(out.getvalue())["target_items"][0]["number"], 23)
            path.write_text(json.dumps(page([item(), item(iid="second")])), encoding="utf-8")
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["--snapshot", str(path)]), 1)
            path.write_text("not-json", encoding="utf-8")
            with redirect_stderr(StringIO()):
                self.assertEqual(main(["--snapshot", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
