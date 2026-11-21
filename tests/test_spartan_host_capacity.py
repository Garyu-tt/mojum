"""Hermetic CPU Legion capacity fixtures. No host resources or credentials used."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "tools" / "spartan_host_capacity.py"
spec = importlib.util.spec_from_file_location("spartan_host_capacity", MODULE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class HostCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.proc = root / "proc"
        self.cgroup = root / "cgroup"
        (self.proc / "self").mkdir(parents=True)
        (self.cgroup / "legion" / "worker").mkdir(parents=True)
        (self.cgroup / "cgroup.controllers").write_text("cpu memory", encoding="ascii")
        (self.proc / "self/cgroup").write_text("0::/legion/worker\n", encoding="ascii")
        (self.proc / "meminfo").write_text("MemAvailable: 8388608 kB\n", encoding="ascii")
        self.leaf = self.cgroup / "legion" / "worker"

    def put(self, name, content, directory=None):
        ((directory or self.leaf) / name).write_text(content, encoding="ascii")

    def estimate(self, **kwargs):
        return probe.estimate(proc_root=self.proc, cgroup_root=self.cgroup,
                              affinity_cpus=16, **kwargs)

    def test_affinity_and_reserves_limit_slots_without_cgroups(self):
        result = self.estimate(worker_cpu_milli=1000, worker_mem_mib=256, reserve_cpu_milli=2000)
        self.assertEqual(result["planned_slot_ceiling"], 14)
        self.assertFalse(result["capacity_is_enforced"])
        self.assertEqual(result["running_model_sessions"], "not measured")

    def test_fractional_cpu_quota_limits_candidate_workers(self):
        self.put("cpu.max", "250000 100000")
        result = self.estimate(worker_cpu_milli=250, reserve_cpu_milli=500)
        self.assertEqual(result["effective_cpu_milli"], 2500)
        self.assertEqual(result["planned_slot_ceiling"], 8)

    def test_stricter_parent_quota_is_applied(self):
        self.put("cpu.max", "400000 100000")
        self.put("cpu.max", "150000 100000", self.cgroup / "legion")
        result = self.estimate(worker_cpu_milli=250, reserve_cpu_milli=0)
        self.assertEqual(result["planned_slot_ceiling"], 6)

    def test_memory_free_under_hierarchical_limit(self):
        self.put("memory.max", str(8 * probe.MIB))
        self.put("memory.current", str(6 * probe.MIB))
        self.put("memory.max", str(10 * probe.MIB), self.cgroup / "legion")
        self.put("memory.current", str(7 * probe.MIB), self.cgroup / "legion")
        result = self.estimate(worker_mem_mib=512, reserve_mem_mib=1024,
                               reserve_cpu_milli=0)
        self.assertEqual(result["available_memory_mib"], 2)
        self.assertEqual(result["planned_slot_ceiling"], 0)

    def test_available_memory_matches_proc_when_unlimited(self):
        self.put("memory.max", "max")
        result = self.estimate(worker_mem_mib=1024, reserve_mem_mib=1024,
                               reserve_cpu_milli=0)
        self.assertEqual(result["available_memory_mib"], 8192)
        self.assertEqual(result["planned_slot_ceiling"], 7)

    def test_memory_cap_is_fail_closed_without_current(self):
        self.put("memory.max", "8192")
        with self.assertRaisesRegex(probe.ProbeError, "memory.current"):
            self.estimate()

    def test_malformed_cpu_quota_is_fail_closed(self):
        for raw in ("bad", "0 100000", "100000 0", "100000 10foo", "max nope"):
            with self.subTest(raw=raw):
                self.put("cpu.max", raw)
                with self.assertRaises(probe.ProbeError):
                    self.estimate()

    def test_unavailable_v2_mount_refuses_to_guess(self):
        (self.cgroup / "cgroup.controllers").unlink()
        with self.assertRaisesRegex(probe.ProbeError, "v2"):
            self.estimate()

    def test_no_relative_escape_from_cgroup_root(self):
        (self.proc / "self/cgroup").write_text("0::/../../etc\n", encoding="ascii")
        with self.assertRaises(probe.ProbeError):
            self.estimate()

    def test_invalid_config_rejected_before_resource_probe(self):
        for config in ({"worker_cpu_milli": 0}, {"worker_mem_mib": -1},
                       {"reserve_mem_mib": -1}, {"max_workers": 301},
                       {"worker_cpu_milli": True}):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    self.estimate(**config)

    def test_small_affinity_and_large_reserve_returns_zero(self):
        result = probe.estimate(proc_root=self.proc, cgroup_root=self.cgroup,
                                affinity_cpus=1)
        self.assertEqual(result["planned_slot_ceiling"], 0)

    def test_parent_memory_ceiling_even_if_leaf_unlimited(self):
        self.put("memory.max", "max")
        self.put("memory.max", str(32 * probe.MIB), self.cgroup / "legion")
        self.put("memory.current", str(1 * probe.MIB), self.cgroup / "legion")
        result = self.estimate(worker_mem_mib=1, reserve_mem_mib=0,
                               reserve_cpu_milli=0)
        self.assertEqual(result["planned_slot_ceiling"], 16)
        self.assertTrue(result["cgroup_memory_cap_seen"])


if __name__ == "__main__":
    unittest.main()
