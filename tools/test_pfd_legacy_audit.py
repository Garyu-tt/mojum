import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pfd_legacy_audit import analyze


class StaticInventoryTest(unittest.TestCase):
    def fixture(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, source in files.items():
            path = root / 'src' / 'mojum' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding='utf-8')
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Audit', '-c', 'user.email=audit@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        return root

    def test_duplicate_module_name_is_not_successful_inventory(self):
        root = self.fixture({'__init__.py': '', 'alpha.py': 'import mojum.beta\n', 'alpha/__init__.py': '', 'beta.py': ''})
        report = analyze(root)
        self.assertTrue(report['parse_errors'])
        self.assertIn('alpha.py', report['parse_errors'][0]['error'])
        self.assertIn('alpha/__init__.py', report['parse_errors'][0]['error'])
        self.assertNotIn('mojum.alpha', report['standalone_candidates'])
        output = root / 'report.json'
        completed = subprocess.run([sys.executable, str(Path(__file__).with_name('pfd_legacy_audit.py')), str(root), '--output', str(output)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(output.read_text())['parse_errors'], report['parse_errors'])

    def test_normal_graph_preserves_cycle_and_edges(self):
        root = self.fixture({'__init__.py': '', 'a.py': 'from . import b\n', 'b.py': 'import mojum.a\n', 'c.py': ''})
        report = analyze(root)
        self.assertEqual(report['parse_errors'], [])
        self.assertEqual(report['module_count'], 4)
        self.assertEqual(report['edge_count'], 2)
        self.assertEqual(report['cycles'], [['mojum.a', 'mojum.b']])
        self.assertIn('mojum.c', report['standalone_candidates'])

    def test_inventory_uses_committed_tree_not_worktree(self):
        root = self.fixture({'__init__.py': '', 'a.py': 'import mojum.b\n', 'b.py': ''})
        baseline = analyze(root)
        (root / 'src/mojum/a.py').write_text('import os\n', encoding='utf-8')
        (root / 'src/mojum/b.py').unlink()
        (root / 'src/mojum/untracked.py').write_text('def :\n', encoding='utf-8')
        for name in ('tests/test_untracked.py', '.github/workflows/untracked.yml'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('', encoding='utf-8')
        self.assertEqual(analyze(root), baseline)

    def test_syntax_error_remains_fail_closed(self):
        root = self.fixture({'__init__.py': '', 'broken.py': 'def :\n'})
        report = analyze(root)
        self.assertEqual(report['parse_errors'][0]['module'], 'mojum.broken')
        self.assertNotIn('mojum.broken', report['standalone_candidates'])


if __name__ == '__main__':
    unittest.main()
