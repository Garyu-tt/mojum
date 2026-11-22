"""Read-only registry audit for *planned*, never auto-installed integrations.

Never imports a third-party package and never connects to external systems.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "src" / "mojum" / "data" / "optional_integrations.json"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_catalog(path=DEFAULT_CATALOG):
    """Fail closed on ambiguous, oversized or non-optional dependency entries."""
    blob = Path(path).read_bytes()
    if len(blob) > 256 * 1024:
        raise ValueError("oversized integration registry")
    try:
        data = json.loads(blob, object_pairs_hook=_reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid integration JSON") from exc
    if type(data) is not dict or set(data) != {
        "schema", "default_enabled", "cpu_baseline", "entries",
    }:
        raise ValueError("invalid integration registry")
    if (data["schema"] != "mojum.optional-integrations/1"
            or data["default_enabled"] is not False
            or data["cpu_baseline"] != "stdlib-unchanged"):
        raise ValueError("integrations must remain opt-in")
    entries = data["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= 128:
        raise ValueError("invalid integration entries")
    seen_keys, seen_issues = set(), set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != {
            "key", "issue", "priority", "status", "optional",
            "license_review", "packages",
        }:
            raise ValueError("invalid integration entry fields")
        key, issue = entry["key"], entry["issue"]
        if type(key) is not str or _NAME.fullmatch(key) is None or key in seen_keys:
            raise ValueError("invalid or duplicate integration key")
        if type(issue) is not int or not 1 <= issue <= 2**31 - 1 or issue in seen_issues:
            raise ValueError("invalid or duplicate GitHub issue")
        seen_keys.add(key)
        seen_issues.add(issue)
        if (entry["priority"] not in ("P0", "P1", "P2")
                or entry["status"] not in ("planned", "contract", "experiment", "accepted")
                or entry["optional"] is not True
                or entry["license_review"] not in ("required", "approved")):
            raise ValueError("invalid integration gates")
        packages = entry["packages"]
        if type(packages) is not list or not 1 <= len(packages) <= 16:
            raise ValueError("invalid package collection")
        seen_packages = set()
        for package in packages:
            if type(package) is not dict or set(package) != {"name", "url"}:
                raise ValueError("invalid package")
            name, url = package["name"], package["url"]
            if type(name) is not str or _NAME.fullmatch(name) is None or name in seen_packages:
                raise ValueError("invalid or repeated package name")
            seen_packages.add(name)
            if type(url) is not str or len(url) > 2048:
                raise ValueError("invalid package reference")
            target = urlsplit(url)
            if (target.scheme != "https" or not target.netloc
                    or target.username or target.password or target.fragment):
                raise ValueError("unsafe package reference")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit planned optional Mojum integrations")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--json", action="store_true", help="print checked entries as JSON")
    args = parser.parse_args(argv)
    try:
        data = load_catalog(args.catalog)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"integration catalog rejected: {exc}\n")
    if args.json:
        print(json.dumps(data, sort_keys=True, ensure_ascii=False))
    else:
        for entry in data["entries"]:
            print(f'#{entry["issue"]} {entry["priority"]} {entry["key"]}: '
                  f'{entry["status"]} ({len(entry["packages"])} candidate packages)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
