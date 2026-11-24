"""Adversarial tests for the Spartan Bubblewrap argv planner."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "spartan_sandbox.py"
SPEC = importlib.util.spec_from_file_location("spartan_sandbox", MODULE)
sandbox = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sandbox
SPEC.loader.exec_module(sandbox)


class SandboxPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.scratch = self.root / "scratch"
        self.repo.mkdir()
        self.scratch.mkdir(mode=0o700)
        if os.name == "posix":
            self.scratch.chmod(0o700)
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        self.runner = self.bin / "test-runner"
        self.runner.write_text("#!/bin/false\\n", encoding="utf-8")
        self.runner.chmod(0o700)
        self.fake_tools = []
        for name in ("bwrap", "prlimit", "timeout"):
            tool = self.root / name
            tool.write_text("#!/bin/false\\n", encoding="utf-8")
            tool.chmod(0o700)
            self.fake_tools.append(tool)
        self.limits = sandbox.SandboxLimits(
            cpu_seconds=30, memory_bytes=512 * 1024**2,
            process_count=32, open_files=128, wall_seconds=45,
        )

    def tearDown(self):
        self.temp.cleanup()

    def build(self, command=None, limits=None, **kwargs):
        return sandbox.build_sandbox_argv(
            self.repo, self.scratch,
            command if command is not None else [
                str(self.runner), "--verbose", "a;not-a-shell-command"
            ],
            limits if limits is not None else self.limits,
            bwrap=kwargs.get("bwrap", self.fake_tools[0]),
            prlimit=kwargs.get("prlimit", self.fake_tools[1]),
            timeout=kwargs.get("timeout", self.fake_tools[2]),
        )

    def test_valid_plan_is_argv_only_and_has_required_isolation(self):
        argv = self.build()
        self.assertEqual(argv[0], str(self.fake_tools[1]))
        self.assertIn("--cpu=30", argv)
        self.assertIn("--as=536870912", argv)
        self.assertIn("--nproc=32", argv)
        self.assertIn("--nofile=128", argv)
        self.assertIn("45s", argv)
        self.assertIn("--unshare-all", argv)
        self.assertIn("--unshare-net", argv)
        self.assertIn("--clearenv", argv)
        self.assertIn("--ro-bind", argv)
        self.assertIn(str(self.repo), argv)
        self.assertIn(str(self.scratch), argv)
        self.assertIn("a;not-a-shell-command", argv)
        self.assertNotIn("shell=True", argv)

    def test_source_is_read_only_and_only_scratch_is_writable(self):
        argv = self.build()
        ro = argv.index("--ro-bind")
        self.assertEqual(argv[ro + 1:ro + 3], ["/usr", "/usr"])
        self.assertEqual(argv.count("--ro-bind"), 2)
        self.assertEqual(argv[argv.index("--bind") + 1:argv.index("--bind") + 3],
                         [str(self.scratch), "/scratch"])

    def test_rejects_bad_numeric_limits_including_bool(self):
        bad = (
            sandbox.SandboxLimits(True, 512 * 1024**2, 32, 128, 45),
            sandbox.SandboxLimits(30, 512 * 1024**2, 32, 128, 0),
            sandbox.SandboxLimits(30, 1024, 32, 128, 45),
            sandbox.SandboxLimits(30, 512 * 1024**2, 513, 128, 45),
            sandbox.SandboxLimits(30, 512 * 1024**2, 32, 15, 45),
            sandbox.SandboxLimits(30, 512 * 1024**2, 32, 128, 3601),
        )
        for limits in bad:
            with self.subTest(limits=limits), self.assertRaises(sandbox.SandboxError):
                self.build(limits=limits)

    def test_rejects_relative_symlink_and_overlapping_paths(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.build_sandbox_argv("relative", self.scratch, [str(self.runner)], self.limits)
        link = self.root / "scratch-link"
        link.symlink_to(self.scratch, target_is_directory=True)
        with self.assertRaises(sandbox.SandboxError):
            sandbox.build_sandbox_argv(self.repo, link, [str(self.runner)], self.limits,
                                        bwrap=self.fake_tools[0], prlimit=self.fake_tools[1],
                                        timeout=self.fake_tools[2])
        with self.assertRaises(sandbox.SandboxError):
            sandbox.build_sandbox_argv(self.repo, self.repo / "nested", [str(self.runner)], self.limits)

    def test_rejects_unbounded_or_non_native_command_values(self):
        for command in ([], [True], [str(self.runner), "\\x00"], [str(self.runner)] + ["x"] * 128):
            with self.subTest(command=repr(command)), self.assertRaises(sandbox.SandboxError):
                self.build(command=command)

    def test_rejects_relative_missing_or_out_of_workspace_executable(self):
        for command in (["python", "-m", "unittest"], ["/missing/test-runner"],
                        ["/bin/sh", "-c", "id"]):
            with self.subTest(command=command), self.assertRaises(sandbox.SandboxError):
                self.build(command=command)

    def test_rejects_non_executable_tool_paths(self):
        nonexec = self.root / "not-executable"
        nonexec.write_text("not executable", encoding="utf-8")
        with self.assertRaises(sandbox.SandboxError):
            self.build(bwrap=nonexec)

    def test_sandbox_environment_does_not_inherit_parent_secrets(self):
        env = sandbox.sandbox_environment()
        self.assertEqual(set(env), {"PATH", "HOME", "LANG", "GIT_TERMINAL_PROMPT"})
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("SSH_AUTH_SOCK", env)

    def test_refuses_foreign_limits_record_shape(self):
        with self.assertRaises(sandbox.SandboxError):
            self.build(limits={"cpu_seconds": 30})


if __name__ == "__main__":
    unittest.main()
