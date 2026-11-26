"""Offline, read-only host capability preflight for Mojum.

No optional modules are imported and no network or filesystem writes occur.
This is advisory metadata, not a guarantee that a simulator can start.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sqlite3
import sys
from typing import Any

SCHEMA = "mojum.host-preflight/1"
OPTIONAL_MODULES = ("mujoco", "numpy", "onnxruntime", "torch")


def collect() -> dict[str, Any]:
    """Collect non-identifying capabilities without probing user files."""
    optional = {}
    for name in OPTIONAL_MODULES:
        try:
            optional[name] = importlib.util.find_spec(name) is not None
        except (ImportError, AttributeError, ValueError):
            optional[name] = False
    cpu_count = os.cpu_count()
    return {
        "schema": SCHEMA,
        "python": {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
            "supported": sys.version_info >= (3, 11),
        },
        "platform": platform.system().lower(),
        "cpu_logical": cpu_count if isinstance(cpu_count, int) and cpu_count > 0 else None,
        "sqlite_version": sqlite3.sqlite_version,
        "optional_modules": optional,
        "offline": True,
        "advisory_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only offline Mojum host preflight")
    parser.add_argument("--strict", action="store_true", help="exit nonzero if Python < 3.11")
    args = parser.parse_args(argv)
    result = collect()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2 if args.strict and not result["python"]["supported"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
