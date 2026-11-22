"""Offline admission checks, without host access or dependencies."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "cpu_legion_capacity.py"
spec = importlib.util.spec_from_file_location("cpu_legion_capacity", MODULE_PATH)
legion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legion)


def inventory():
    return {
        "schema": legion.INVENTORY_SCHEMA,
        "target_workers": 7,
        "worker": {"cpu_millicores": 1000, "memory_mib": 2048},
        "nodes": [
            {"id": "z-host", "cpu_millicores": 4000, "memory_mib": 8192,
             "reserve_cpu_millicores": 1000, "reserve_memory_mib": 2048,
             "max_workers": 3},
            {"id": "a-host", "cpu_millicores": 8000, "memory_mib": 6144,
             "reserve_cpu_millicores": 0, "reserve_memory_mib": 2048,
             "max_workers": 8},
        ],
    }


class CapacityTests(unittest.TestCase):
    def test_cpu_and_ram_minimum_both_apply(self):
        result = legion.plan(inventory())
        self.assertEqual((result["admitted"], result["unplaced"]), (5, 2))
        self.assertEqual([(n["id"], n["capacity"]) for n in result["nodes"]],
                         [("a-host", 2), ("z-host", 3)])
        for n in result["nodes"]:
            self.assertLessEqual(n["planned_cpu_millicores"], n["usable_cpu_millicores"])
            self.assertLessEqual(n["planned_memory_mib"], n["usable_memory_mib"])

    def test_round_robin_and_input_order_independent(self):
        src = inventory()
        first = legion.plan(src, target=3)
        self.assertEqual([n["assigned"] for n in first["nodes"]], [2, 1])
        src["nodes"].reverse()
        self.assertEqual(first, legion.plan(src, target=3))

    def test_disable_host_and_zero_target(self):
        src = inventory()
        src["nodes"][0]["enabled"] = False
        self.assertEqual(legion.plan(src)["admitted"], 2)
        result = legion.plan(src, target=0)
        self.assertEqual((result["admitted"], result["unplaced"]), (0, 0))

    def test_worker_cap_and_reserved_capacity(self):
        src = inventory()
        src["nodes"][0]["max_workers"] = 1
        src["nodes"][1]["reserve_memory_mib"] = 6144
        self.assertEqual(legion.plan(src)["admitted"], 1)

    def test_at_most_300_admissions(self):
        src = inventory()
        src["target_workers"] = 300
        src["nodes"] = [{"id": "huge", "cpu_millicores": 1_000_000,
                         "memory_mib": 1_000_000,
                         "reserve_cpu_millicores": 0, "reserve_memory_mib": 0,
                         "max_workers": 300}]
        result = legion.plan(src)
        self.assertEqual(result["admitted"], 300)
        with self.assertRaises(legion.InventoryError):
            legion.plan(src, target=301)

    def test_ambiguous_and_invalid_types_fail_closed(self):
        base = inventory()
        cases = [
            lambda d: d.update(target_workers=True),
            lambda d: d.update(schema="unknown"),
            lambda d: d.update(extra=1),
            lambda d: d["worker"].update(memory_mib=0),
            lambda d: d["worker"].update(cpu_millicores=1.5),
            lambda d: d["nodes"][0].update(enabled=1),
            lambda d: d["nodes"][0].update(id="../escape"),
            lambda d: d["nodes"][0].update(reserve_memory_mib=999999),
            lambda d: d["nodes"][0].update(untrusted_field=True),
            lambda d: d["nodes"].append(dict(d["nodes"][0])),
            lambda d: d.update(nodes=[]),
            lambda d: d["nodes"][0].update(max_workers=301),
        ]
        for mutate in cases:
            data = json.loads(json.dumps(base))
            mutate(data)
            with self.subTest(data=data), self.assertRaises(legion.InventoryError):
                legion.plan(data)

    def test_json_duplicate_fields_nan_utf8_and_size_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "inventory.json"
            for payload in (b'{"schema":1,"schema":2}', b'{"target_workers":NaN}',
                            b'\xff', b'{', b'x' * (legion.MAX_INPUT_BYTES + 1)):
                with self.subTest(payload=payload[:30]):
                    path.write_bytes(payload)
                    with self.assertRaises(legion.InventoryError):
                        legion.load_inventory(path)

    @unittest.skipUnless(hasattr(os, "mkfifo") and getattr(os, "O_NONBLOCK", 0),
                         "POSIX FIFO support required")
    def test_inventory_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "inventory.fifo"
            os.mkfifo(path)
            # A regression to a blocking Path.open must fail on timeout,
            # not wedge the entire GitHub Actions runner.
            completed = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--inventory", str(path)],
                text=True, capture_output=True, check=False, timeout=5,
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertIn("regular file", completed.stderr)

    @unittest.skipUnless(getattr(os, "O_NOFOLLOW", 0),
                         "platform lacks O_NOFOLLOW")
    def test_inventory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            valid = Path(root) / "valid.json"
            valid.write_text(json.dumps(inventory()), encoding="utf-8")
            linked = Path(root) / "linked.json"
            linked.symlink_to(valid)
            with self.assertRaisesRegex(legion.InventoryError, "cannot read inventory"):
                legion.load_inventory(linked)

    def test_oversized_integer_literal_normalizes_parser_value_error(self):
        if not hasattr(sys, "get_int_max_str_digits"):
            self.skipTest("interpreter has no digit conversion limit")
        maximum = sys.get_int_max_str_digits()
        if maximum == 0 or maximum + 1 > legion.MAX_INPUT_BYTES:
            self.skipTest("integer conversion limit is disabled or above input budget")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "integer.json"
            path.write_text('{"target_workers":' + '9' * (maximum + 1) + '}',
                            encoding="utf-8")
            with self.assertRaisesRegex(legion.InventoryError, "valid UTF-8 JSON"):
                legion.load_inventory(path)

    def test_cli_returns_machine_readable_plan_and_strict_shortfall(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "inventory.json"
            path.write_text(json.dumps(inventory()), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = legion.main(["--inventory", str(path), "--require-full"])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(stdout.getvalue())["unplaced"], 2)
            with redirect_stdout(io.StringIO()):
                rc = legion.main(["--inventory", str(path), "--target", "3", "--require-full"])
            self.assertEqual(rc, 0)

    def test_cli_invalid_input_does_not_emit_partial_json(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "invalid.json"
            path.write_text("{}", encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = legion.main(["--inventory", str(path)])
            self.assertEqual(rc, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("cpu-legion:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
