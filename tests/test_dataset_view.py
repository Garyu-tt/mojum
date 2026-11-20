import unittest
from mojum.dataset_view import project_episodes


def event(id="a", source="synthetic", visibility="PUBLIC", tick=1, observation="pulse"):
    return dict(id=id, source=source, visibility=visibility, tick=tick, observation=observation)


class DatasetViewTests(unittest.TestCase):
    def test_order_and_duplicate_stability(self):
        a, b = event(), event(id="b", tick=0)
        first = project_episodes([a, b, a], allowed_sources={"synthetic"})
        second = project_episodes([b, a], allowed_sources={"synthetic"})
        self.assertEqual(first, second)
        self.assertEqual(len(first["records"]), 2)

    def test_private_filtered(self):
        result = project_episodes([event(visibility="PRIVATE"), event(id="b", source="other")], allowed_sources={"synthetic"})
        self.assertEqual(result["records"], [])

    def test_conflicting_duplicate(self):
        with self.assertRaises(ValueError):
            project_episodes([event(), event(observation="other")], allowed_sources={"synthetic"})

    def test_duplicate_identity_cannot_downgrade_visibility(self):
        """A PUBLIC record cannot shadow the same PRIVATE/RESTRICTED event ID."""
        public = event(id="sensitive", observation="sanitized")
        for visibility in ("PRIVATE", "RESTRICTED"):
            hidden = event(id="sensitive", visibility=visibility, observation="private text")
            for pair in ((hidden, public), (public, hidden)):
                with self.subTest(visibility=visibility, first=pair[0]["visibility"]):
                    with self.assertRaisesRegex(ValueError, "conflicting duplicate id"):
                        project_episodes(list(pair), allowed_sources={"synthetic"})

    def test_excluded_source_cannot_shadow_allowed_id(self):
        """Identity collision checks must precede policy filtering."""
        public = event(id="shared-id", source="synthetic")
        excluded = event(id="shared-id", source="untrusted")
        for pair in ((excluded, public), (public, excluded)):
            with self.subTest(first=pair[0]["source"]):
                with self.assertRaisesRegex(ValueError, "conflicting duplicate id"):
                    project_episodes(list(pair), allowed_sources={"synthetic"})

    def test_identical_private_duplicates_remain_filtered(self):
        private = event(visibility="PRIVATE")
        self.assertEqual(
            project_episodes([private, dict(private)], allowed_sources={"synthetic"})["records"],
            [],
        )

    def test_budget(self):
        with self.assertRaises(ValueError):
            project_episodes([event()], allowed_sources={"synthetic"}, max_records=0)

    def test_invalid_schema(self):
        for item in [dict(event(), secret="x"), dict(event(), tick=True), dict(event(), visibility="UNKNOWN")]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                project_episodes([item], allowed_sources={"synthetic"})

    def test_reject_unbounded(self):
        with self.assertRaises(ValueError):
            project_episodes([event()] * 100001, allowed_sources={"synthetic"})


if __name__ == "__main__":
    unittest.main()
