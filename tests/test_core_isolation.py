"""The extracted core must work without installed domain packages."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORE_SRC = Path(__file__).resolve().parents[1] / "src" / "mojum_core"

# Fresh isolated interpreter, no inherited PYTHONPATH or site packages.
# Record attempted non-core imports even if production code catches ImportError.
PROBE = r'''
import sys
from importlib.abc import MetaPathFinder
from pathlib import Path

attempts = []
class CoreOnly(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition('.')[0]
        if root != 'mojum_core' and root not in sys.stdlib_module_names:
            attempts.append(fullname)
            raise ImportError('non-core dependency: ' + fullname)

sys.path.insert(0, sys.argv[1])
sys.meta_path.insert(0, CoreOnly())
from mojum_core import graph, executor, sdk, loader

for module in (graph, executor, sdk, loader):
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())

registry = sdk.Registry()
registry.register({
    'schema': 'mojum.plugin/1', 'id': 'probe', 'version': '1.0.0',
    'api_major': 1, 'nodes': {
        'probe.emit': {'inputs': {}, 'outputs': {'x': 'value/1'}},
        'probe.read': {'inputs': {'x': 'value/1'}, 'outputs': {}},
    }
}, {
    'probe.emit': lambda i, c, t, ns: ({'x': t}, []),
    'probe.read': lambda i, c, t, ns: ({}, [{'value': i['x']}]),
}, revision='1' * 40)
runtime = registry.executor({
    'schema': 'mechanicus.universal-graph/1', 'id': 'isolated', 'revision': '1',
    'nodes': [
        {'id': 'a', 'kind': 'probe.emit', 'config': {}, 'notes': {'purpose': 'probe'}},
        {'id': 'b', 'kind': 'probe.read', 'config': {}, 'notes': {'purpose': 'read'}},
    ],
    'edges': [{'from': {'node': 'a', 'port': 'x'}, 'to': {'node': 'b', 'port': 'x'}}]
})
assert runtime.step()['effects'] == [{'value': 0}]
assert runtime.step()['effects'] == [{'value': 1}]
assert not attempts, attempts
assert not any(n == 'mojum' or n.startswith(('mojum.', 'mojum_compat')) for n in sys.modules)
'''


class CoreIsolationTests(unittest.TestCase):
    def test_generic_execution_with_only_core_and_stdlib(self):
        with tempfile.TemporaryDirectory() as work:
            shutil.copytree(CORE_SRC, Path(work) / 'mojum_core',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            result = subprocess.run(
                [sys.executable, '-I', '-S', '-c', PROBE, work],
                cwd=work, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
