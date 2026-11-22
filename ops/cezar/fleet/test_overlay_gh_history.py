"""Static security contract for read-only injected fleet UI.

Executable browser tests remain a separate QA step; these assertions enforce
the narrow safety boundary even when the upstream Cezar web bundle changes.
"""
from pathlib import Path
import unittest


JS = Path(__file__).with_name("overlay.js").read_text(encoding="utf-8")


class GitHubFleetOverlaySourceTests(unittest.TestCase):
    def test_public_record_links_are_bounded_and_escaped(self):
        self.assertIn("Number.isSafeInteger(entry.number)", JS)
        self.assertIn('link.textContent="#"+entry.number+', JS)
        self.assertIn('link.rel="noopener noreferrer"', JS)
        self.assertIn('https://github.com/Garyu-tt/mojum/', JS)
        self.assertNotIn("innerHTML", JS)
        self.assertNotIn("document.write(", JS)

    def test_reports_distinct_sources_without_fake_cezar_tasks(self):
        self.assertIn('История Cezar (не весь Mojum)', JS)
        self.assertIn('Открытые PR GitHub', JS)
        self.assertIn('Открытые Issues GitHub', JS)
        self.assertIn('Новые открытые PR (GitHub)', JS)
        self.assertIn('Недавние закрытые / слитые PR (GitHub)', JS)
        self.assertIn('Внешние прямо сейчас', JS)
        self.assertIn('неизвестно', JS)
        self.assertIn('Mojum · ', JS)

    def test_read_only_overlay(self):
        self.assertIn('cache:"no-store"', JS)
        self.assertIn('credentials:"same-origin"', JS)
        self.assertIn('redirect:"error"', JS)
        self.assertNotIn('method:"POST"', JS)
        self.assertNotIn('method:"PUT"', JS)
        self.assertNotIn('method:"DELETE"', JS)
        self.assertNotIn('localStorage', JS)
        self.assertNotIn('eval(', JS)


if __name__ == "__main__":
    unittest.main()
