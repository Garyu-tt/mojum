"""Regression for bounded dataset identifiers after PUBLIC byte-budget QA."""
import unittest

from mojum.dataset_view import project_episodes


def episode(**changes):
    item = {"id": "e1", "source": "synthetic", "visibility": "PUBLIC",
            "tick": 1, "observation": "pulse"}
    item.update(changes)
    return item


class DatasetIdentifierBoundsTests(unittest.TestCase):
    def test_long_identifiers_fail_even_when_hidden(self):
        for visibility in ("PUBLIC", "PRIVATE", "RESTRICTED"):
            for field in ("id", "source"):
                with self.subTest(visibility=visibility, field=field):
                    with self.assertRaisesRegex(ValueError, "invalid " + field):
                        project_episodes([episode(visibility=visibility, **{field: "x" * 257})],
                                         allowed_sources={"synthetic"})

    def test_allowlist_is_bounded_before_projection(self):
        for value in ("", "x" * 257):
            with self.subTest(value_length=len(value)):
                with self.assertRaisesRegex(ValueError, "invalid allowed_sources"):
                    project_episodes([], allowed_sources={value})

    def test_utf8_validation_and_valid_unicode_boundary(self):
        good = episode(id="🎨" * 256, source="учеба")
        result = project_episodes([good], allowed_sources={"учеба"})
        self.assertEqual(result["records"][0]["id"], good["id"])
        for field in ("id", "source"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "UTF-8"):
                project_episodes([episode(**{field: "bad\\ud800".encode("ascii").decode("unicode_escape")})],
                                 allowed_sources={"synthetic"})


if __name__ == "__main__":
    unittest.main()
