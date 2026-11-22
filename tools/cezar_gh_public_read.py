#!/usr/bin/python3
"""Cezar-only, public and READ-ONLY GitHub CLI subset for one pinned repository.

Never authenticates, writes, pushes, approves, merges or prints credentials.
Only supported gh repo view and gh api GET patterns are allowed. Other gh
commands fail closed. This is NOT the actual authenticated GitHub CLI.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "Garyu-tt/mojum"
BASE = "https://api.github.com"
CACHE_DIR = Path("/var/lib/cezar/home/public-gh-cache")
MAX_BODY = 1500000
MAX_ARG = 1200


class Refused(ValueError):
    pass


def parse(args):
    if args == ["repo", "view", "--json", "nameWithOwner"]:
        return "repo", None, False
    if len(args) >= 4 and args[:3] == ["api", "--method", "GET"]:
        include = False
        path = args[3]
        tail = args[4:]
    elif len(args) >= 5 and args[0:2] == ["api", "--include"] and args[2:4] == ["--method", "GET"]:
        include = True
        path = args[4]
        tail = args[5:]
    else:
        raise Refused("only documented public read operations are allowed")
    if any(len(a) > MAX_ARG for a in args) or len(args) > 22:
        raise Refused("argument limit exceeded")
    params = {}
    while tail:
        if len(tail) < 2:
            raise Refused("invalid flag")
        flag, value, *tail = tail
        if flag == "-H":
            if value != "Accept: application/vnd.github+json":
                raise Refused("unexpected API header")
            continue
        if flag != "-f" or "=" not in value:
            raise Refused("unsupported API arguments")
        key, val = value.split("=", 1)
        if key in params or key not in ("q", "per_page", "sort", "order", "page"):
            raise Refused("unsupported API parameter")
        params[key] = val

    if path == "/search/issues":
        q = params.get("q", "")
        if not q.startswith("repo:" + REPO + " "):
            raise Refused("unapproved repository scope")
        if "is:issue" not in q and "is:pr" not in q:
            raise Refused("missing issue or PR qualifier")
        if len(q) > 400:
            raise Refused("query too large")
        # The leading repo: qualifier alone is not a scope boundary: an
        # additional repo: or a Boolean OR can broaden GitHub search.
        # This shim is only authorized to read the single pinned repository.
        remainder = q[len("repo:" + REPO + " "):]
        if re.search(r"(?i)(?:repo|org|user):|\b(?:OR|NOT)\b|[()]", remainder):
            raise Refused("query may broaden repository scope")
        sort = params.get("sort", "updated")
        order = params.get("order", "asc")
        if sort not in ("updated", "created") or order != "asc":
            raise Refused("unexpected search order")
        per_page = bounded_int(params.get("per_page", "5"), 1, 100)
        page = bounded_int(params.get("page", "1"), 1, 10)
        query = {"q": q, "per_page": str(per_page), "sort": sort,
                 "order": order, "page": str(page)}
        url = BASE + path + "?" + urllib.parse.urlencode(query)
        return "api", (url, 90), include

    match = re.fullmatch(r"repos/Garyu-tt/mojum/issues/([1-9][0-9]{0,6})/timeline", path)
    if match:
        if any(k not in ("per_page", "page") for k in params):
            raise Refused("invalid timeline parameters")
        per_page = bounded_int(params.get("per_page", "100"), 1, 100)
        page = bounded_int(params.get("page", "1"), 1, 10)
        url = BASE + "/" + path + "?" + urllib.parse.urlencode(
            {"per_page": per_page, "page": page})
        return "api", (url, 600), include
    raise Refused("unapproved public API endpoint")


def bounded_int(value, minval, maxval):
    if not value.isascii() or not value.isdecimal() or len(value) > 4:
        raise Refused("invalid integer")
    result = int(value)
    if not minval <= result <= maxval:
        raise Refused("integer outside limits")
    return result


def read(url, ttl):
    if not url.startswith(BASE + "/"):
        raise Refused("unexpected API origin")
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if CACHE_DIR.is_symlink() or CACHE_DIR.stat().st_mode & 0o077:
        raise Refused("cache directory not owner-private")
    token = hashlib.sha256(url.encode()).hexdigest()
    p = CACHE_DIR / (token + ".json")
    if p.is_file() and not p.is_symlink() and time.time() - p.stat().st_mtime < ttl:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data["body"].encode("utf-8"), data["link"]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    req = urllib.request.Request(url, headers={
        "User-Agent": "cezar-mojum-public-read-only/0.1",
        "Accept": "application/vnd.github+json",
    })
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPSHandler())
    with client.open(req, timeout=14) as response:
        if response.status != 200 or response.geturl() != url:
            raise Refused("unexpected GitHub response or redirect")
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise Refused("GitHub response too large")
        link = response.headers.get("Link", "")[:3000]
    # Reject unexpected payload shape early instead of caching HTML/errors.
    json.loads(raw)
    data = {"body": raw.decode("utf-8"), "link": link}
    tmp = CACHE_DIR / (token + ".tmp." + str(os.getpid()))
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file)
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return raw, link


def main(args):
    if args == ["--version"] or args == ["version"]:
        os.execv("/usr/bin/gh", ["/usr/bin/gh", "--version"])
    if args[:2] == ["auth", "status"]:
        print("Cezar public read adapter has NO GitHub authorization. "
              "Writes are disabled; authorize the actual gh CLI separately.",
              file=sys.stderr)
        return 1
    try:
        mode, target, include = parse(args)
        if mode == "repo":
            sys.stdout.write(json.dumps({"nameWithOwner": REPO}) + "\n")
        else:
            raw, link = read(*target)
            prefix = b""
            if include:
                header = "HTTP/2.0 200 OK\n"
                if link:
                    header += "Link: " + link + "\n"
                prefix = (header + "\n").encode("utf-8")
            sys.stdout.buffer.write(prefix + raw + b"\n")
        return 0
    except (Refused, OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        print("Cezar public GitHub read denied/unavailable: "
              + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
