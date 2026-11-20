"""Static syntax/contract checks for the offline observation browser module."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "examples/infant_observer.html"


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.scripts = []
        self.external_sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            props = dict(attrs)
            if "src" in props:
                self.external_sources.append(props["src"])
            else:
                self.inside = True
                self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.scripts[-1] += data


class ObserverBrowserSyntaxTests(unittest.TestCase):
    def test_inline_javascript_parses_in_node_if_installed(self):
        page = PAGE.read_text(encoding="utf-8")
        parser = _Scripts()
        parser.feed(page)
        self.assertEqual(parser.external_sources, [])
        self.assertEqual(len(parser.scripts), 1)
        js = parser.scripts[0]
        for token in (
            "function renderMilestones()", "function pollLive()", "function show(i)",
            "spectator_scene", "first_repeated_action", "first_blocked_motion",
            "first_confirmed_prediction", "first_prediction_surprise",
            'addEventListener("click"', "textContent", "showOpenFilePicker",
        ):
            self.assertIn(token, js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("fetch(", js)
        self.assertNotIn("WebSocket(", js)
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js not installed; structural browser checks remain active")
        process = subprocess.run(
            [node, "--check", "-"],
            input=js, text=True, capture_output=True, timeout=12,
        )
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
