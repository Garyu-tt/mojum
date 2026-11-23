"""Static safety and packaging tests for the standalone graph preview."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "tools" / "graph_preview" / "index.html"
README = ROOT / "tools" / "graph_preview" / "README.md"


class GraphPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_single_file_is_self_contained(self):
        self.assertIn('<script>', self.html)
        self.assertIn('<style>', self.html)
        self.assertNotRegex(self.html, r'<script[^>]+src=')
        self.assertNotRegex(self.html, r'<link[^>]+href=')
        self.assertNotRegex(self.html, r'@import|https?://')

    def test_no_dynamic_code_or_html_injection_sinks(self):
        self.assertNotRegex(self.html, r'\\beval\\s*\\(')
        self.assertNotRegex(self.html, r'new\\s+Function\\s*\\(')
        self.assertNotIn("innerHTML", self.html)
        self.assertIn("textContent", self.html)
        self.assertIn("createElementNS", self.html)

    def test_bounded_admission_is_present(self):
        self.assertIn("512 * 1024", self.html)
        self.assertIn("MAX_NODES = 256", self.html)
        self.assertIn("MAX_EDGES = 2048", self.html)
        self.assertIn("Number.isFinite", self.html)
        self.assertIn("JSON.parse", self.html)

    def test_read_only_and_not_authentication_claims(self):
        self.assertIn("READ ONLY", self.html)
        self.assertIn("не подтверждает подлинность SHA-256", self.html)
        self.assertIn("No graph writeback", self.readme)
        self.assertIn("not verified/authenticated evidence", self.readme)

    def test_accessible_controls_and_status(self):
        self.assertIn('type="file"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-label="Фильтр узлов"', self.html)
        self.assertIn('role="button"', self.html)

    def test_no_external_capability_or_mutation_surface(self):
        self.assertNotRegex(self.html, r'fetch\\s*\\(|XMLHttpRequest|WebSocket|localStorage|indexedDB')
        self.assertIn("MAX_BYTES", self.html)
        self.assertIn("execution", self.readme.lower())


if __name__ == "__main__":
    unittest.main()
