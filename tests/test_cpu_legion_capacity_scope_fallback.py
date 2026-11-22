"""Regression: an incomplete existing cgroup scope must not fall back to root."""

import tempfile
import unittest
from pathlib import Path

from mojum.cpu_legion_capacity import CapacityError, discover


class CgroupScopeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "cgroup"
        self.root.mkdir()
        self.proc = base / "proc-cgroup"
        self.meminfo = base / "meminfo"
        self.meminfo.write_text("MemAvailable: 8192000 kB\n", encoding="ascii")
        self.proc.write_text("0::/service.slice/worker\n", encoding="ascii")
        # Deliberately permissive root values expose an optimistic fallback.
        (self.root / "cpu.max").write_text("max 100000\n", encoding="ascii")
        (self.root / "memory.max").write_text("max\n", encoding="ascii")
        (self.root / "memory.current").write_text("0\n", encoding="ascii")

    def detect(self):
        return discover(
            meminfo=self.meminfo,
            proc_cgroup=self.proc,
            cgroup_root=self.root,
            affinity_cores=8,
        )

    def test_existing_incomplete_scope_is_not_mistaken_for_mount_root(self):
        member = self.root / "service.slice" / "worker"
        member.mkdir(parents=True)
        # The scope exists, but cpu.max cannot be read. Root must not stand in.
        with self.assertRaisesRegex(CapacityError, "scope resource files unavailable"):
            self.detect()

    def test_existing_scope_with_cpu_but_missing_memory_is_rejected(self):
        member = self.root / "service.slice" / "worker"
        member.mkdir(parents=True)
        (member / "cpu.max").write_text("100000 100000\n", encoding="ascii")
        with self.assertRaises(CapacityError):
            self.detect()

    def test_hidden_member_path_keeps_cgroup_namespace_root_fallback(self):
        # In a cgroup namespace the membership path can be absent from the
        # visible mount; root then represents the process's bounded scope.
        snapshot = self.detect()
        self.assertEqual(snapshot["cgroup_scopes_checked"], 1)
        self.assertEqual(snapshot["cpu_millicores"], 8000)


if __name__ == "__main__":
    unittest.main()
