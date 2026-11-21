"""UTF-8 and size bounds apply before privacy/source filtering."""
import unittest

from mojum.dataset_view import project_episodes


def episode(**overrides):
    result = {"id": "event-1", "source": "synthetic", "visibility": "PUBLIC",
              "tick": 1, "observation": "pulse"}
    result.update(overrides)
    return result


class DatasetViewTextSafetyTests(unittest.TestCase):
    def test_unpaired_surrogates_are_rejected_for_all_text_fields(self):
        for visibility in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            for field in ("id", "source", "observation"):
                with self.subTest(visibility=visibility, field=field):
                    with self.assertRaisesRegex(ValueError, f"invalid {field}"):
                        project_episodes([episode(visibility=visibility, **{field: "bad\ud800"})],
                                         allowed_sources={"synthetic"})

    def test_surrogates_rejected_even_for_disallowed_source(self):
        with self.assertRaisesRegex(ValueError, "invalid observation"):
            project_episodes([episode(source="other", observation="bad\udfff")],
                             allowed_sources={"synthetic"})

    def test_sources_allowlist_is_also_checked(self):
        for invalid in ("\ud800", "a" * 257, ""):
            with self.subTest(invalid=repr(invalid)), self.assertRaisesRegex(ValueError, "invalid allowed_sources"):
                project_episodes([], allowed_sources={invalid})

    def test_unbounded_identifiers_are_rejected_even_if_private(self):
        for field in ("id", "source"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"invalid {field}"):
                project_episodes([episode(visibility="PRIVATE", **{field: "x" * 257})],
                                 allowed_sources={"synthetic"})

    def test_valid_unicode_and_boundary_sizes_are_stable(self):
        item = episode(id="🪴" * 256, observation="🎵" * 4096)
        first = project_episodes([item], allowed_sources={"synthetic"})
        second = project_episodes([dict(item)], allowed_sources=frozenset({"synthetic"}))
        self.assertEqual(first, second)
        self.assertEqual(first["records"][0]["observation"], item["observation"])


if __name__ == "__main__":
    unittest.main()
