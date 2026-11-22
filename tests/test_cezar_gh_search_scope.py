"""Regression: Cezar public GitHub adapter must never search outside Mojum."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "cezar_gh_public_read.py"
spec = importlib.util.spec_from_file_location("cezar_gh_public_read", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def search(query):
    return adapter.parse(["api", "--method", "GET", "/search/issues",
                          "-f", "q=" + query])


class CezarSearchScopeTests(unittest.TestCase):
    def test_rejects_or_and_extra_repository_qualifiers(self):
        rejected = (
            "repo:Garyu-tt/mojum is:pr OR repo:another/repo is:pr",
            "repo:Garyu-tt/mojum is:issue OR is:pr",
            "repo:Garyu-tt/mojum is:pr -repo:Garyu-tt/mojum",
            "repo:Garyu-tt/mojum is:pr repo:another/repo",
            "repo:Garyu-tt/mojum is:pr unrelated",
            "repo:Garyu-tt/mojum notis:pr",
            "repo:Garyu-tt/mojum is:pr is:pr",
            "repo:Garyu-tt/mojum is:issue OR label:cezar-ready",
            "repo:Garyu-tt/mojum is:pr author:other-user",
        )
        for query in rejected:
            with self.subTest(query=query), self.assertRaises(adapter.Refused):
                search(query)

    def test_preserves_bounded_public_issue_pr_polling(self):
        accepted = (
            "repo:Garyu-tt/mojum is:pr",
            "repo:Garyu-tt/mojum is:issue created:>=2026-11-20",
            "repo:Garyu-tt/mojum is:issue is:open label:cezar-ready",
            "repo:Garyu-tt/mojum is:pr updated:>=2026-11-20 -label:cezar-hold",
        )
        for query in accepted:
            with self.subTest(query=query):
                mode, target, include = search(query)
                self.assertEqual(mode, "api")
                self.assertTrue(target[0].startswith("https://api.github.com/search/issues?"))
                self.assertFalse(include)


if __name__ == "__main__":
    unittest.main()
