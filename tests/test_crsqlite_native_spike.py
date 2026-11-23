import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from mojum.crsqlite_native_spike import _run_bounded_child, run_native_probe


class CrSqliteNativeSpikeTests(unittest.TestCase):
    def test_extension_cannot_autoload_or_use_relative_path(self):
        with self.assertRaises(ValueError):
            run_native_probe("crsqlite.so", "0" * 64)
        with self.assertRaises(ValueError):
            run_native_probe("/tmp/crsqlite.so", "not-a-hash")
        with self.assertRaises(ValueError):
            run_native_probe("/tmp/crsqlite.so", "0" * 64, timeout_s=True)

    def test_rejects_untrusted_extension_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "crsqlite.so"
            path.write_bytes(b"synthetic, not a shared library")
            with mock.patch("mojum.crsqlite_native_spike._run_bounded_child") as execute:
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    run_native_probe(str(path), "a" * 64)
                self.assertFalse(execute.called)

    def test_malformed_binary_fails_without_disclosing_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "crsqlite.so"
            data = b"not a native extension"
            path.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError, "probe failed") as err:
                run_native_probe(str(path), hashlib.sha256(data).hexdigest())
            self.assertNotIn(str(path), str(err.exception))

    def test_forbids_other_names_and_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "lib-not-approved.so"
            data = b"test"
            source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            with self.assertRaises(ValueError):
                run_native_probe(str(source), digest)
            link = Path(td) / "crsqlite.so"
            try:
                link.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlink not supported")
            with self.assertRaises(ValueError):
                run_native_probe(str(link), digest)

    def test_subprocess_is_opt_in_and_has_private_copy(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "crsqlite.so"
            data = b"audited synthetic fixture"
            source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()

            def subprocess_fixture(args, **kwargs):
                self.assertEqual(args[:4], [os.sys.executable, "-I", "-S", "-c"])
                self.assertEqual(Path(args[-1]).read_bytes(), data)
                self.assertNotEqual(Path(args[-1]), source)
                self.assertEqual(kwargs["timeout"], 15)
                self.assertEqual(kwargs["env"], {"PYTHONNOUSERSITE": "1"})
                return mock.Mock(returncode=0, stdout=b'{"native_converged": true, "rows": 3, "upstream_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}')

            with mock.patch("mojum.crsqlite_native_spike._run_bounded_child", side_effect=subprocess_fixture) as run:
                result = run_native_probe(str(source), digest)
                self.assertTrue(result["native_converged"])
                self.assertEqual(run.call_count, 1)

    def test_failed_native_timeout_is_not_success(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "crsqlite.so"
            payload = b"bounded native fixture"
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            from subprocess import TimeoutExpired
            with mock.patch("mojum.crsqlite_native_spike._run_bounded_child", side_effect=TimeoutExpired("probe", 1)):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    run_native_probe(str(path), digest, timeout_s=1)

    def test_real_child_output_is_bounded_before_buffering(self):
        # The native extension is not needed to test the subprocess boundary.
        with tempfile.TemporaryDirectory() as td:
            for target in ("stdout", "stderr"):
                with self.subTest(stream=target), self.assertRaisesRegex(
                    RuntimeError, "output limit"
                ):
                    _run_bounded_child(
                        [os.sys.executable, "-I", "-S", "-c",
                         "import sys; getattr(sys, %r).write('X' * 65536)" % target],
                        cwd=td, env={"PYTHONNOUSERSITE": "1"}, timeout=5,
                    )

    def test_native_version_unverified_is_not_auto_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "crsqlite.so"
            payload = b"fixture"
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            with mock.patch("mojum.crsqlite_native_spike._run_bounded_child", return_value=mock.Mock(
                returncode=0, stdout=b'{"native_converged": false}', stderr=b""
            )):
                with self.assertRaisesRegex(RuntimeError, "missing convergence proof"):
                    run_native_probe(str(path), digest)


if __name__ == "__main__":
    unittest.main()
