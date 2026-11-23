"""Run a Python unittest target with SQLite ResourceWarning promoted to failure.

Designed for CPython 3.13 where unclosed sqlite3.Connection emits ResourceWarning.
No application modules are imported; all target arguments are passed to unittest.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def build_command(python: str, targets: list[str]) -> list[str]:
    if not python:
        raise ValueError("python executable must be nonempty")
    if not targets:
        raise ValueError("at least one unittest target is required")
    if any(not item or item.startswith("-") for item in targets):
        raise ValueError("targets must be unittest names, not flags")
    return [
        python,
        "-X", "tracemalloc=25",
        "-W", "error::ResourceWarning",
        "-m", "unittest", *targets,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("targets", nargs="+", help="unittest names (e.g. discover is not supported)")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    env["PYTHONWARNINGS"] = "error::ResourceWarning"
    # The target is a separate process, so no project state is mutated by this runner.
    completed = subprocess.run(build_command(args.python, args.targets), env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
