# Spartan credential-free test sandbox profile R0.1

This is a command planner for the execution-isolation work package in issue
#478. It produces an argv for a Linux Bubblewrap sandbox wrapped by GNU
prlimit and timeout. It does not launch the command itself.

## Isolation profile

- Requires an explicit absolute repository directory and a separate explicit
  scratch directory owned by the caller with POSIX mode 0700.
- Mounts the complete source repository read-only at /workspace.
- The only writable host-directory mount is the caller-owned scratch at
  /scratch; /tmp and the minimal /dev are disposable sandbox filesystems.
- Mounts /usr read-only for the runtime. It does not automatically mount
  host HOME, credential stores, SSH socket paths or other host directories.
- Uses Bubblewrap namespace isolation, including an explicit network namespace
  with no network access, a new session and die-with-parent behavior.
- Clears the inherited environment and explicitly sets PATH, HOME, TMPDIR,
  LANG and GIT_TERMINAL_PROMPT. Bubblewrap additionally supplies
  PWD=/workspace for the sandbox working directory.
- Applies CPU time, virtual address-space, process-count and file-descriptor
  limits through prlimit, plus a wall-clock deadline through GNU timeout.
- Uses argv arrays and never invokes a shell.

The caller must supply a trusted, sanitized credential-free source checkout
and a fresh empty private scratch directory. All contents of the explicit
source, scratch and runtime mounts remain visible inside the sandbox;
read-only mounting does not redact files. The planner validates paths and
scratch ownership/mode, but does not inspect or sanitize directory contents,
reject hardlinks or socket nodes, or defend against same-UID path replacement.
Keep those paths stable and free of credentials, live sockets and private data
before execution.

## Example

The following is illustrative. The paths must exist and the three system
executables must be installed. A real test executable must be inside the
read-only repository or the mounted /usr tree.

    limits = SandboxLimits(
        cpu_seconds=60,
        memory_bytes=2 * 1024**3,
        process_count=64,
        open_files=256,
        wall_seconds=90,
    )
    argv = build_sandbox_argv(
        "/srv/mojum-task-worktrees/issue-478-sandbox",
        "/srv/mojum-scratch/task-478",
        ["/usr/bin/python3", "-m", "unittest", "discover", "-s", "tests"],
        limits,
    )
    result = subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True,
        timeout=limits.wall_seconds + 5, check=False,
        env=sandbox_environment(), shell=False,
    )

The helper's return value is only a proposed command. The caller must check
that the host supports the required namespace operations, run it in an
operator-approved environment, and treat nonzero exit, timeout, or missing
Bubblewrap/prlimit/timeout as a hard failure. Never fall back to direct test
execution.

## Explicit limitations

- This module does not provision a Git worktree, apply a patch, inspect patch
  contents, or publish a PR. Use the existing worktree helper and patch guard
  as separate trusted stages.
- Bubblewrap is not a substitute for a VM or a hardened multi-tenant container
  against kernel vulnerabilities. Same-UID processes and host administrators
  remain outside this boundary.
- The /usr runtime mount is read-only but broad. Operators should use a
  dedicated minimal runtime image for higher-risk workloads.
- No cgroup CPU quota or resident-set hard limit is claimed. prlimit applies
  per-process resource limits; the host executor must enforce cgroup-level
  aggregate CPU/RAM quotas when needed.
- The focused CI workflow installs Bubblewrap and runs a disposable isolated
  Python smoke command alongside the planner tests. This does not activate a
  production sandbox.
