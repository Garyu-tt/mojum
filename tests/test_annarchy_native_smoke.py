import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tools.annarchy_native_smoke import (
    MAX_OUTPUT_BYTES, PROBE_SCHEMA, SCHEMA, SmokeError, _environment,
    _validate_result, run_native_smoke,
)


def result_payload(**changes):
    value = {
        "schema": PROBE_SCHEMA,
        "mode": "shadow",
        "backend": "annarchy",
        "tick_count": 8,
        "neurons": 2,
        "dt_ms": 0.5,
        "tau_ms": 10.0,
        "input_sha256": "a" * 64,
        "reference_sha256": "b" * 64,
        "observed_sha256": "c" * 64,
        "final_rates": [0.2, 0.4],
        "max_abs_error": 0.0,
        "motor_intention": None,
        "physical_contact": False,
    }
    value.update(changes)
    return value


class FakeProcess:
    def __init__(self, command, *, env, **kwargs):
        self.returncode = 0
        self.pid = 2_147_483_647
        self.command = command
        self.kwargs = kwargs
        Path(env["MOJUM_SMOKE_RESULT"]).write_text(
            json.dumps(result_payload(tick_count=32)), encoding="utf-8"
        )

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class NativeSmokeContractTests(unittest.TestCase):
    def test_accepts_exact_contract(self):
        parsed = _validate_result(
            json.dumps(result_payload()).encode(), frames=8, tolerance=1e-5
        )
        self.assertEqual(parsed["backend"], "annarchy")

    def test_rejects_wrong_identity_and_authority(self):
        for changes in (
            {"backend": "brian2"},
            {"schema": "wrong/1"},
            {"motor_intention": {"move": 1}},
            {"physical_contact": True},
            {"tick_count": True},
            {"neurons": 3},
            {"max_abs_error": True},
            {"max_abs_error": 1e-3},
            {"final_rates": [0.2, float("nan")]},
            {"observed_sha256": "Z" * 64},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(SmokeError):
                    _validate_result(
                        json.dumps(result_payload(**changes)).encode(),
                        frames=8, tolerance=1e-5,
                    )

    def test_rejects_oversized_and_non_json_output(self):
        with self.assertRaises(SmokeError):
            _validate_result(b" " * (MAX_OUTPUT_BYTES + 1), frames=8, tolerance=1e-5)
        for raw in (b"", b"not-json", b"NaN", b"[]"):
            with self.subTest(raw=raw[:12]), self.assertRaises(SmokeError):
                _validate_result(raw, frames=8, tolerance=1e-5)

    def test_rejects_duplicate_json_keys(self):
        raw = b'{"schema":"mojum.neural.rate-shadow-probe/1","schema":"forged"}'
        with self.assertRaises(SmokeError):
            _validate_result(raw, frames=8, tolerance=1e-5)

    def test_environment_does_not_inherit_credentials_or_proxies(self):
        with mock.patch.dict(os.environ, {
            "GITHUB_TOKEN": "do-not-copy",
            "AWS_SECRET_ACCESS_KEY": "do-not-copy",
            "HTTPS_PROXY": "http://proxy.invalid",
            "PATH": "/usr/bin",
        }, clear=True):
            env = _environment("/tmp/smoke-home", "/tmp/result.json", "/tmp/peer/src")
        self.assertEqual(env["PATH"], "/usr/bin")
        for key in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["OMP_NUM_THREADS"], "1")
        self.assertEqual(env["PYTHONPATH"], "/tmp/peer/src")

    def test_rejects_unbounded_parameters_before_start(self):
        with mock.patch("tools.annarchy_native_smoke.subprocess.Popen") as popen:
            for kwargs in (
                {"frames": 0}, {"frames": 65}, {"wall_seconds": 0},
                {"cpu_seconds": 121}, {"memory_bytes": 1},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(SmokeError):
                    run_native_smoke(**kwargs)
            popen.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "OS resource limits require POSIX")
    def test_runs_through_child_process_and_returns_bounded_receipt(self):
        with mock.patch("tools.annarchy_native_smoke.subprocess.Popen", FakeProcess):
            receipt = run_native_smoke(frames=32)
        self.assertEqual(receipt["schema"], SCHEMA)
        self.assertEqual(receipt["status"], "pass")
        self.assertEqual(receipt["backend"], "ANNarchy")
        self.assertFalse(receipt["physical_contact"])
        self.assertIsNone(receipt["motor_intention"])

    def test_failed_child_is_not_reported_as_success(self):
        class FailedProcess(FakeProcess):
            def __init__(self, command, *, env, **kwargs):
                super().__init__(command, env=env, **kwargs)
                self.returncode = 7
        with mock.patch("tools.annarchy_native_smoke.subprocess.Popen", FailedProcess):
            with self.assertRaisesRegex(SmokeError, "exit status 7"):
                run_native_smoke(frames=32)


if __name__ == "__main__":
    unittest.main()
