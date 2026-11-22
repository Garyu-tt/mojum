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
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_catalog(path=DEFAULT_CATALOG):
    """Fail closed on ambiguous, oversized or non-optional dependency entries."""
    # Bound the read itself: an operator-supplied catalog may be arbitrarily large.
    # Reading the entire file before checking len(blob) defeats the memory cap.
    with Path(path).open("rb") as source:
        blob = source.read(256 * 1024 + 1)
    if len(blob) > 256 * 1024:
        raise ValueError("oversized integration registry")
    try:
        data = json.loads(blob, object_pairs_hook=_reject_duplicates)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
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
        if entry["status"] != "planned" and entry["license_review"] != "approved":
            raise ValueError("integration progress requires approved license review")
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
            # This is only a static URL catalog, but malformed HTTPS strings
            # must not be accepted as legitimate upstream package origins.
            if any(ord(ch) < 33 or ord(ch) > 126 for ch in url):
                raise ValueError("unsafe package reference")
            try:
                target = urlsplit(url)
                hostname = target.hostname
                # Accessing .port also rejects malformed numeric port syntax.
                port = target.port
            except ValueError as exc:
                raise ValueError("malformed package reference") from exc
            labels = hostname.split(".") if hostname else []
            if (target.scheme != "https" or not hostname
                    or len(hostname) > 253 or len(labels) < 2
                    or target.netloc != hostname or port is not None
                    or target.username is not None or target.password is not None
                    or target.fragment
                    or any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels)):
                raise ValueError("unsafe package reference")
    # Schema /1 is the 14-issue acceptance map, not an arbitrary URL registry.
    if seen_issues != set(range(689, 703)):
        raise ValueError("incomplete or out-of-scope integration issue map")
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
