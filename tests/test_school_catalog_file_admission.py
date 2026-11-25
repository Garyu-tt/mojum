"""Real catalog file admission, including the writerless FIFO boundary."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mojum.school import MAX_CATALOG_BYTES, SchoolContractError, load_catalog


CATALOG = Path(__file__).resolve().parents[1] / "examples" / "school_catalog_r01.json"


class CatalogFileAdmissionTests(unittest.TestCase):
    def test_regular_catalog_and_size_limit(self):
        expected = load_catalog(CATALOG)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_bytes(CATALOG.read_bytes())
            self.assertEqual(load_catalog(path), expected)
            path.write_bytes(b" " * (MAX_CATALOG_BYTES + 1))
            with self.assertRaisesRegex(SchoolContractError, "byte limit"):
                load_catalog(path)

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"),
                         "requires POSIX FIFO and nonblocking open")
    def test_writerless_fifo_rejected_by_both_clis_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "catalog"
            os.mkfifo(fifo)
            output = Path(directory) / "output"
            for module, args in (
                ("mojum.school", ["--section", "music", "--out", str(output)]),
                ("mojum.school_verify", ["--fixture", str(output)]),
            ):
                with self.subTest(module=module):
                    result = subprocess.run(
                        [sys.executable, "-m", module, "--catalog", str(fifo), *args],
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("school catalog must be a regular file", result.stderr)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
