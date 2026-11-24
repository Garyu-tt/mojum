"""Fail-closed argv planning for credential-free Linux test sandboxes.

The returned command requires Bubblewrap, prlimit and GNU timeout. Building an
argv does not execute it or prove that a sandbox is active.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Sequence


MAX_ARGV_ITEMS = 128
MAX_ARG_LENGTH = 4096
MAX_WALL_SECONDS = 3600
MAX_CPU_SECONDS = 3600
MAX_MEMORY_BYTES = 64 * 1024**3
MAX_PROCESSES = 512
MAX_OPEN_FILES = 4096


class SandboxError(ValueError):
    """A sandbox specification cannot be safely admitted."""


@dataclass(frozen=True)
class SandboxLimits:
    cpu_seconds: int
    memory_bytes: int
    process_count: int
    open_files: int
    wall_seconds: int


def _integer(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise SandboxError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _real_directory(value: str | os.PathLike[str], name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise SandboxError(f"{name} must be absolute")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise SandboxError(f"{name} path contains a symlink")
    try:
        path.lstat()
    except OSError as exc:
        raise SandboxError(f"{name} is unavailable") from exc
    if path.is_symlink() or not path.is_dir():
        raise SandboxError(f"{name} must be a real directory, not a symlink")
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxError(f"{name} cannot be resolved") from exc


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _runtime_symlinks() -> list[tuple[str, str]]:
    """Return only usr-merge aliases whose resolved targets remain under /usr."""
    usr = Path("/usr").resolve(strict=True)
    aliases: list[tuple[str, str]] = []
    for name in ("bin", "lib", "lib64"):
        host_path = Path("/") / name
        if not host_path.exists() and not host_path.is_symlink():
            continue
        try:
            target = host_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SandboxError(f"/{name} runtime alias cannot be resolved") from exc
        if not _within(target, usr):
            raise SandboxError(f"/{name} must resolve inside /usr for this sandbox profile")
        aliases.append((name, str(target)))
    return aliases


def _private_scratch(path: Path) -> None:
    if os.name == "posix":
        info = path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SandboxError("scratch directory must be owned by caller and mode 0700")


def _absolute_file(value: str | os.PathLike[str], name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise SandboxError(f"{name} must be an absolute executable path")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxError(f"{name} executable is unavailable") from exc
    if not resolved.is_file():
        raise SandboxError(f"{name} must be a regular executable file")
    if os.name == "posix" and not os.access(resolved, os.X_OK):
        raise SandboxError(f"{name} is not executable")
    return resolved


def _validate_command(command: Sequence[str], repository: Path) -> list[str]:
    if type(command) not in (list, tuple) or not 1 <= len(command) <= MAX_ARGV_ITEMS:
        raise SandboxError("command must be a bounded argv list")
    result: list[str] = []
    for item in command:
        if type(item) is not str or not item or len(item) > MAX_ARG_LENGTH or "\x00" in item:
            raise SandboxError("command arguments must be bounded nonempty strings")
        result.append(item)
    executable = Path(result[0])
    if not executable.is_absolute():
        raise SandboxError("test executable must be an absolute path")
    try:
        resolved = executable.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxError("test executable does not exist") from exc
    if not resolved.is_file() or (os.name == "posix" and not os.access(resolved, os.X_OK)):
        raise SandboxError("test executable must be an executable regular file")
    runtime_root = Path("/usr").resolve()
    workspace_executable = _within(resolved, repository)
    if not _within(resolved, runtime_root) and not workspace_executable:
        raise SandboxError("test executable must be inside /usr or the read-only workspace")
    if resolved.name.lower() in {"sh", "bash", "dash", "zsh", "fish", "csh", "tcsh"}:
        raise SandboxError("shell interpreters are not admitted as test entrypoints")
    # Host repository paths are not mounted at their original location inside
    # Bubblewrap. Translate an executable inside the repository to its
    # read-only /workspace mount; /usr executables retain their runtime path.
    if workspace_executable:
        result[0] = "/workspace/" + resolved.relative_to(repository).as_posix()
    else:
        result[0] = str(resolved)
    return result


def build_sandbox_argv(
    repository: str | os.PathLike[str],
    scratch: str | os.PathLike[str],
    command: Sequence[str],
    limits: SandboxLimits,
    *,
    bwrap: str | os.PathLike[str] = "/usr/bin/bwrap",
    prlimit: str | os.PathLike[str] = "/usr/bin/prlimit",
    timeout: str | os.PathLike[str] = "/usr/bin/timeout",
) -> list[str]:
    """Build the exact argv for a no-network, credential-cleared test sandbox.

    Source is mounted read-only at /workspace. Only the caller-owned scratch
    directory is writable at /scratch. No host HOME, environment, credentials,
    sockets, devices (beyond a minimal synthetic /dev), or network are mounted.
    """
    if os.name != "posix" or not Path("/proc").is_dir() or not Path("/usr").is_dir():
        raise SandboxError("this sandbox profile requires a Linux/POSIX host")
    if type(limits) is not SandboxLimits:
        raise SandboxError("native SandboxLimits required")
    cpu = _integer(limits.cpu_seconds, "cpu_seconds", 1, MAX_CPU_SECONDS)
    memory = _integer(limits.memory_bytes, "memory_bytes", 64 * 1024**2, MAX_MEMORY_BYTES)
    processes = _integer(limits.process_count, "process_count", 1, MAX_PROCESSES)
    files = _integer(limits.open_files, "open_files", 16, MAX_OPEN_FILES)
    wall = _integer(limits.wall_seconds, "wall_seconds", 1, MAX_WALL_SECONDS)

    repo = _real_directory(repository, "repository")
    write_root = _real_directory(scratch, "scratch")
    if repo == write_root or _within(write_root, repo) or _within(repo, write_root):
        raise SandboxError("scratch and repository must be disjoint directories")
    _private_scratch(write_root)

    bwrap_path = _absolute_file(bwrap, "bwrap")
    prlimit_path = _absolute_file(prlimit, "prlimit")
    timeout_path = _absolute_file(timeout, "timeout")
    argv = _validate_command(command, repo)

    runtime_links = _runtime_symlinks()
    bubblewrap = [
        str(bwrap_path),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--unshare-net",
        "--clearenv",
        "--ro-bind", "/usr", "/usr",
        *[arg for name, target in runtime_links for arg in ("--symlink", target, f"/{name}")],
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--dir", "/workspace",
        "--ro-bind", str(repo), "/workspace",
        "--dir", "/scratch",
        "--bind", str(write_root), "/scratch",
        "--dir", "/tmp/home",
        "--chdir", "/workspace",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/home",
        "--setenv", "TMPDIR", "/scratch",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "GIT_TERMINAL_PROMPT", "0",
        "--",
        *argv,
    ]
    return [
        str(prlimit_path),
        f"--cpu={cpu}",
        f"--as={memory}",
        f"--nproc={processes}",
        f"--nofile={files}",
        "--",
        str(timeout_path),
        "--signal=TERM",
        "--kill-after=2s",
        f"{wall}s",
        *bubblewrap,
    ]


def sandbox_environment() -> dict[str, str]:
    """Minimal outer environment for subprocess.run; never inherit secrets."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
    }
