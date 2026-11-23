"""Isolated regression tests for disposable Aquarium cold-restart audit."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from offline_restart_smoke import SmokeFailure, _checkpoint, run_audit, SCHEMA


def _fixture(tick, **patch):
    value = {
        "schema": "mojum.aquarium.checkpoint/1", "tick": tick,
        "stored_events": tick + 1, "neural_mode": "disabled",
        "trust": "checksum_only_not_authenticated",
        "agent_id": "fixture-only", "journal_head": format(tick + 1, "064x"),
        "manifest_sha256": "a" * 64, "status": "SYNTHETIC_PLACEHOLDER_NOT_NEURAL_OR_MUJOCO",
        "sim_time_ns": tick * 10_000_000,
        "world_revision": hashlib.sha256(f"synthetic-{tick}".encode()).hexdigest(),
        "genome_revision": "b" * 64,
    }
    value.update(patch)
    return json.dumps(value)


class StubRunner:
    def __init__(self, patch=None, *, fail_on=None):
        self.calls = []
        self.patch = patch or {}
        self.fail_on = fail_on

    def __call__(self, command, *, cwd, env, capture_output, text, timeout, check):
        self.calls.append(command)
        if self.fail_on == len(self.calls):
            return subprocess.CompletedProcess(command, 7, "", "PRIVATE /tmp/path")
        assert cwd and env["PYTHONPATH"].endswith("src")
        assert capture_output and text and not check and timeout >= 5
        db = Path(command[command.index("--db") + 1])
        # sqlite3.Connection context manager commits but does NOT close.
        with closing(sqlite3.connect(db)) as con:
            con.execute("CREATE TABLE IF NOT EXISTS marker (id INT)")
            con.commit()
        tick_batch = int(command[command.index("--ticks") + 1])
        previous = self.calls[-2] if len(self.calls) > 1 else None
        tick = sum(int(call[call.index("--ticks") + 1]) for call in self.calls)
        if previous:
            assert command[command.index("--expected-head") + 1] == format(
                tick - tick_batch + 1, "064x")
        payload_obj = json.loads(_fixture(tick))
        payload_obj.update(self.patch)
        payload = json.dumps(payload_obj)
        return subprocess.CompletedProcess(command, 0, payload, "")


class RestartAuditTests(unittest.TestCase):
    def test_success_three_processes_without_private_report(self):
        stub = StubRunner()
        result = run_audit(first_ticks=4, second_ticks=5, runner=stub)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["stored_events"], 10)
        self.assertEqual(len(stub.calls), 3)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertNotIn("fixture-only", json.dumps(result))
        self.assertEqual(stub.calls[-1][stub.calls[-1].index("--ticks") + 1], "0")

    def test_subprocess_nonzero_fail_closed_without_stderr_leak(self):
        stub = StubRunner(fail_on=2)
        with self.assertRaisesRegex(SmokeFailure, "exited unsuccessfully") as ctx:
            run_audit(runner=stub)
        self.assertNotIn("PRIVATE", str(ctx.exception))

    def test_wrong_and_forged_structural_fields_rejected(self):
        invalid = [
            {"tick": True}, {"stored_events": 2}, {"trust": "authenticated"},
            {"journal_head": "0" * 63}, {"agent_id": ""},
            {"schema": "mojum.aquarium.checkpoint/999"}, {"status": "READY"},
            {"world_revision": True}, {"genome_revision": 0},
            {"manifest_sha256": "A" * 64},
        ]
        for patch in invalid:
            with self.subTest(patch=patch), self.assertRaises(SmokeFailure):
                run_audit(runner=StubRunner(patch))

    def test_invalid_json_duplicate_members_and_oversize(self):
        with self.assertRaises(SmokeFailure):
            _checkpoint('{"tick":1,"tick":1}', tick=1, events=2)
        with self.assertRaises(SmokeFailure):
            _checkpoint('{' + ' ' * 65536 + '}', tick=1, events=2)
        with self.assertRaises(SmokeFailure):
            _checkpoint('{"tick":NaN}', tick=1, events=2)

    def test_world_revision_is_digest_and_zero_tick_must_preserve_it(self):
        decoded = _checkpoint(_fixture(3), tick=3, events=4)
        self.assertEqual(len(decoded["world_revision"]), 64)
        self.assertEqual(len(decoded["genome_revision"]), 64)
        for invalid in (7, True, "a" * 63, "G" * 64):
            with self.subTest(invalid=invalid), self.assertRaises(SmokeFailure):
                _checkpoint(_fixture(3, world_revision=invalid), tick=3, events=4)

        class AlteredZeroTick(StubRunner):
            def __call__(self, *args, **kwargs):
                proc = super().__call__(*args, **kwargs)
                if len(self.calls) == 3:
                    payload = json.loads(proc.stdout)
                    payload["world_revision"] = "0" * 64
                    return subprocess.CompletedProcess(proc.args, 0, json.dumps(payload), "")
                return proc

        with self.assertRaisesRegex(SmokeFailure, "zero-tick reopen changed world revision"):
            run_audit(runner=AlteredZeroTick())

    def test_bounded_deep_json_and_invalid_utf8_fail_closed(self):
        # About 30 KiB, below MAX_OUTPUT, but beyond JSON nesting limits.
        deep = "[" * 15000 + "0" + "]" * 15000
        for bad in (deep, "\ud800"):
            with self.subTest(kind="nesting" if bad is deep else "surrogate"):
                with self.assertRaises(SmokeFailure):
                    _checkpoint(bad, tick=1, events=2)

    def test_subprocess_decode_error_is_typed_without_leaking_bytes(self):
        def invalid_utf8(*args, **kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with self.assertRaisesRegex(SmokeFailure, "subprocess could not complete"):
            run_audit(runner=invalid_utf8)

    def test_bounded_cli_batches_and_timeouts(self):
        for inputs in ({"first_ticks": 0}, {"second_ticks": 33},
                       {"first_ticks": True}, {"timeout": 0},
                       {"timeout": 121}, {"timeout": 5.0}):
            with self.subTest(inputs=inputs), self.assertRaises(SmokeFailure):
                run_audit(runner=StubRunner(), **inputs)

    def test_timeout_failure(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("synthetic", 5)
        with self.assertRaisesRegex(SmokeFailure, "could not complete"):
            run_audit(runner=timeout)

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[1] / "src/mojum/aquarium.py").is_file(),
        "full Mojum source checkout is required",
    )
    def test_real_aquarium_cli_cold_restart(self):
        report = run_audit(first_ticks=2, second_ticks=1, timeout=60)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["final_tick"], 3)
        self.assertEqual(report["stored_events"], 4)

    def test_real_stdout_shape(self):
        state = _checkpoint(_fixture(3), tick=3, events=4)
        self.assertEqual(state["stored_events"], 4)


if __name__ == "__main__":
    unittest.main()
