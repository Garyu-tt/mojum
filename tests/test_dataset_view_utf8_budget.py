"""Independent regression for dataset-view Unicode and bounded UTF-8 hashing."""
import hashlib
import json
import unittest
from unittest.mock import patch

from mojum import dataset_view


def event(**changes):
    value = dict(id="a", source="synthetic", visibility="PUBLIC", tick=1, observation="pulse")
    value.update(changes)
    return value


class DatasetViewUTF8BudgetTests(unittest.TestCase):
    def test_public_hash_matches_original_canonical_wire(self):
        events = [event(id="unicode-🦋", observation="звук, 音楽, 🎨\n"), event(id="b", tick=0)]
        result = dataset_view.project_episodes(events, allowed_sources={"synthetic"})
        canonical = json.dumps(
            {"schema": dataset_view.SCHEMA, "records": result["records"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(result["sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(result, dataset_view.project_episodes(list(reversed(events)), allowed_sources={"synthetic"}))

    def test_lone_surrogates_fail_closed_in_all_fields_and_visibility(self):
        for visibility in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            for field in ("id", "source", "observation"):
                with self.subTest(visibility=visibility, field=field):
                    with self.assertRaisesRegex(ValueError, "UTF-8"):
                        dataset_view.project_episodes(
                            [event(visibility=visibility, **{field: "invalid-\ud800"})],
                            allowed_sources={"synthetic"},
                        )
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            dataset_view.project_episodes([], allowed_sources={"bad\udfff"})

    def test_streaming_byte_budget_checks_serialized_bytes_not_chars(self):
        tiny = [event(observation="🎨" * 24)]
        with patch.object(dataset_view, "_MAX_CANONICAL_BYTES", 140):
            with self.assertRaisesRegex(ValueError, "byte budget"):
                dataset_view.project_episodes(tiny, allowed_sources={"synthetic"})
        with patch.object(dataset_view, "_MAX_CANONICAL_BYTES", 2048):
            self.assertEqual(len(dataset_view.project_episodes(tiny, allowed_sources={"synthetic"})["records"]), 1)

    def test_large_private_record_is_not_emitted_or_counted_in_public_budget(self):
        hidden = event(id="secret", visibility="PRIVATE", observation="x" * 4096)
        with patch.object(dataset_view, "_MAX_CANONICAL_BYTES", 140):
            result = dataset_view.project_episodes([hidden], allowed_sources={"synthetic"})
            self.assertEqual(result["records"], [])


if __name__ == "__main__":
    unittest.main()
