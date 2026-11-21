"""Fail-closed episode identifier budgets after the canonical PUBLIC byte cap.

Regression extracted from overlapping draft PR #193 after #194 landed.
"""
import hashlib
import json
import unittest

from mojum.dataset_view import SCHEMA, project_episodes


def event(**updates):
    item = {"id": "evt-1", "source": "synthetic", "visibility": "PUBLIC",
            "tick": 1, "observation": "pulse"}
    item.update(updates)
    return item


class DatasetIdentifierBudgetTests(unittest.TestCase):
    def test_overlong_ids_and_sources_are_rejected_before_visibility_filter(self):
        for visibility in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            for key in ("id", "source"):
                with self.subTest(visibility=visibility, key=key):
                    with self.assertRaisesRegex(ValueError, "identifiers"):
                        project_episodes(
                            [event(visibility=visibility, **{key: "x" * 257})],
                            allowed_sources={"synthetic"},
                        )

    def test_source_allowlist_also_has_per_identifier_budget(self):
        for source in ("x" * 257, "🦋" * 257):
            with self.subTest(source_length=len(source)):
                with self.assertRaisesRegex(ValueError, "allowed_sources"):
                    project_episodes([], allowed_sources={source})

    def test_boundary_unicode_still_projects_with_original_canonical_hash(self):
        item = event(id="🎨" * 256, source="🦋" * 256, observation="звук, 音楽")
        answer = project_episodes([item], allowed_sources={item["source"]})
        self.assertEqual(answer["records"], [
            {"id": item["id"], "source": item["source"], "tick": 1,
             "observation": item["observation"]}
        ])
        canonical = json.dumps({"schema": SCHEMA, "records": answer["records"]},
                               ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        self.assertEqual(answer["sha256"], hashlib.sha256(canonical).hexdigest())

    def test_hidden_source_is_checked_even_when_not_allowed(self):
        with self.assertRaisesRegex(ValueError, "identifiers"):
            project_episodes([event(source="x" * 257, visibility="PRIVATE")],
                             allowed_sources={"synthetic"})

    def test_excluded_public_source_is_checked_before_filtering(self):
        # A filtered-out source is still part of the input trust boundary.
        with self.assertRaisesRegex(ValueError, "identifiers"):
            project_episodes([event(source="x" * 257)],
                             allowed_sources={"synthetic"})

    def test_existing_surrogate_contract_is_not_relaxed(self):
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            project_episodes([event(id="bad\ud800")], allowed_sources={"synthetic"})


if __name__ == "__main__":
    unittest.main()
