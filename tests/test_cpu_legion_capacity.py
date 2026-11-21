"""Hermetic CPU Legion safety preflight tests; no processes are spawned."""

import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from mojum.cpu_legion_capacity import CapacityError, discover, main, plan


class ResourceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "sys" / "fs" / "cgroup"
        self.scope = self.root / "service.slice" / "worker"
        self.scope.mkdir(parents=True)
        self.meminfo = base / "meminfo"
        self.member = base / "cgroup"
        self.meminfo.write_text("MemTotal: 32768000 kB\nMemAvailable: 16384000 kB\n")
        self.member.write_text("0::/service.slice/worker\n")
        self.metrics(self.root, "max 100000", "max", 0)
        self.metrics(self.root / "service.slice", "400000 100000", 12 * 1024**3, 3 * 1024**3)
        self.metrics(self.scope, "250000 100000", 8 * 1024**3, 6 * 1024**3)

    def metrics(self, where, cpu, limit, used):
        where.mkdir(parents=True, exist_ok=True)
        (where / "cpu.max").write_text(cpu)
        (where / "memory.max").write_text(str(limit))
        (where / "memory.current").write_text(str(used))

    def detect(self):
        return discover(meminfo=self.meminfo, proc_cgroup=self.member,
                        cgroup_root=self.root, affinity_cores=8)

    def test_nested_memory_and_cpu_quota(self):
        s = self.detect()
        self.assertEqual(s["cpu_millicores"], 2500)
        self.assertEqual(s["available_memory_mib"], 2048)
        self.assertEqual(s["cgroup_scopes_checked"], 3)

    def test_plan_keeps_reserve_and_does_not_round_up_cpu(self):
        p = plan(self.detect(), target=300, worker_mib=512,
                 worker_millicores=700, reserve_mib=1024,
                 reserve_millicores=1000)
        self.assertEqual(p["safe_budget_slots"], 2)
        self.assertEqual(p["ceilings"], {"cpu": 2, "memory": 2})
        self.assertIn("no_workers_started", p["status"])

    def test_ram_limits_slots(self):
        p = plan(self.detect(), target=300, worker_mib=750,
                 worker_millicores=100, reserve_mib=1024,
                 reserve_millicores=0)
        self.assertEqual(p["safe_budget_slots"], 1)

    def test_memory_exhaustion_gives_zero_slots(self):
        self.metrics(self.scope, "250000 100000", 6 * 1024**3, 7 * 1024**3)
        self.assertEqual(self.detect()["available_memory_mib"], 0)

    def test_parent_limit_cannot_be_ignored(self):
        self.metrics(self.root / "service.slice", "100000 100000", 7 * 1024**3, 7 * 1024**3)
        self.assertEqual(self.detect()["cpu_millicores"], 1000)
        self.assertEqual(self.detect()["available_memory_mib"], 0)

    def test_missing_memavailable_refuses_to_guess(self):
        self.meminfo.write_text("MemFree: 30000000 kB\n")
        with self.assertRaisesRegex(CapacityError, "MemAvailable"):
            self.detect()

    def test_missing_cgroup_file_refuses_to_guess(self):
        (self.scope / "memory.current").unlink()
        with self.assertRaisesRegex(CapacityError, "unavailable"):
            self.detect()

    def test_cgroup_namespace_mount_fallback(self):
        self.member.write_text("0::/path/not-visible-in-container\n")
        s = self.detect()
        self.assertEqual(s["cgroup_scopes_checked"], 1)
        self.assertEqual(s["cpu_millicores"], 8000)

    def test_rejects_invalid_quota_and_membership(self):
        (self.scope / "cpu.max").write_text("250000 0")
        with self.assertRaisesRegex(CapacityError, "zero cpu.max period"):
            self.detect()
        self.member.write_text("2:cpu:/legacy\n")
        with self.assertRaisesRegex(CapacityError, "v2"):
            self.detect()

    def test_rejects_bool_negative_and_oversized_target(self):
        valid = self.detect()
        kwargs = dict(target=1, worker_mib=512, worker_millicores=100,
                      reserve_mib=0, reserve_millicores=0)
        for changed in ({"target": 301}, {"worker_mib": True},
                        {"reserve_mib": -1}, {"worker_millicores": 0}):
            with self.subTest(changed=changed), self.assertRaises(CapacityError):
                plan(valid, **(kwargs | changed))

    def test_cli_reports_unavailable_without_starting_workers(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("mojum.cpu_legion_capacity.discover", side_effect=CapacityError("no v2")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["--worker-mib", "1024", "--worker-millicores", "1000"])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn('"status": "unavailable"', stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
