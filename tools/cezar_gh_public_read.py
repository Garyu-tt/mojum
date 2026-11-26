#!/usr/bin/python3
"""Cezar-only, public and READ-ONLY GitHub CLI subset for one pinned repository.

Never authenticates, writes, pushes, approves, merges or prints credentials.
Only bounded gh repo view, public GET and the native OPEN-only GitHub
tab list translation are allowed. All writes and unknown commands fail closed. This is NOT the actual authenticated GitHub CLI.
"""
from __future__ import annotations

from cezar_public_github_tab import parse_graphql, render_graphql, PublicTabDenied
from cezar_public_ci_checks import parse_checks, render_checks

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject API redirects before urllib can contact an unapproved origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("GitHub API redirect forbidden")


def parse(args):
    if args == ["repo", "view", "--json", "nameWithOwner"]:
        return "repo", None, False
    if args == ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]:
        return "repo_plain", None, False
    if args[:2] == ["api", "graphql"]:
        try:
            return "graphql_checks", parse_checks(args), False
        except PublicTabDenied:
            try:
                return "graphql", parse_graphql(args), False
            except PublicTabDenied as exc:
                raise Refused("unsupported native tab GraphQL read") from exc
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
        if len(q) > 400:
            raise Refused("query too large")
        # A leading repo: token alone does not confine GitHub search; OR,
        # extra repo selectors and unsupported operators must fail closed.
        terms = q.split()
        if not terms or terms[0] != "repo:" + REPO:
            raise Refused("unapproved repository scope")
        if sum(term in ("is:issue", "is:pr") for term in terms[1:]) != 1:
            raise Refused("exactly one issue or PR qualifier is required")
        allowed = {"is:issue", "is:pr", "is:open", "is:closed",
                   "state:open", "state:closed"}
        for term in terms[1:]:
            if term in allowed:
                continue
            if re.fullmatch(r"(?:created|updated|closed):(?:>=|>|<=|<)?[0-9]{4}-[0-9]{2}-[0-9]{2}", term):
                continue
            if re.fullmatch(r"-?label:[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", term):
                continue
            raise Refused("unsupported GitHub search qualifier")
        # The creation-date PR triage defaults to open PRs only when
        # the caller did not explicitly select a state. Never silently add
        # is:open to a historical closed-PR search.
        states = {
            "open" if term in ("is:open", "state:open") else "closed"
            for term in terms
            if term in ("is:open", "state:open", "is:closed", "state:closed")
        }
        if len(states) > 1:
            raise Refused("conflicting GitHub search state filters")
        if ("is:pr" in terms
                and any(term.startswith("created:>=") for term in terms)
                and not states
                and not any(term.startswith("closed:") for term in terms)):
            q += " is:open"
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


def _open_cache_dir():
    """Pin every path component, then only operate relative to this descriptor."""
    if not CACHE_DIR.is_absolute():
        raise Refused("cache directory must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(CACHE_DIR.anchor, flags)
    try:
        # Create missing parents relative to pinned descriptors. Path.mkdir()
        # would follow attacker-swapped ancestors before we could validate them.
        for part in CACHE_DIR.parts[1:]:
            if part in (".", ".."):
                raise Refused("unsafe cache directory component")
            try:
                new_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                new_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = new_fd
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or
                info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise Refused("cache directory not owner-private")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read(url, ttl):
    if not url.startswith(BASE + "/"):
        raise Refused("unexpected API origin")
    try:
        cache_fd = _open_cache_dir()
    except OSError as exc:
        raise Refused("untrusted cache directory") from exc
    try:
        token = hashlib.sha256(url.encode()).hexdigest()
        filename = token + ".json"
        try:
            # O_NONBLOCK is mandatory: checking fstat *after* opening a
            # replaced FIFO would otherwise hang before the type check.
            entry_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW |
                               os.O_NONBLOCK, dir_fd=cache_fd)
        except FileNotFoundError:
            entry_fd = None
        except OSError as exc:
            raise Refused("untrusted cache entry") from exc
        if entry_fd is not None:
            with os.fdopen(entry_fd, "rb") as entry:
                info = os.fstat(entry.fileno())
                if (not stat.S_ISREG(info.st_mode) or
                        info.st_uid != os.geteuid() or info.st_mode & 0o077 or
                        info.st_nlink != 1):
                    raise Refused("cache entry not owner-private and regular")
                age = time.time() - info.st_mtime
                if 0 <= age < ttl:
                    limit = MAX_BODY * 6 + 10000
                    serialized = entry.read(limit + 1)
                    if len(serialized) > limit:
                        raise Refused("cache entry exceeds safe limit")
                    try:
                        data = json.loads(serialized)
                        body = data["body"]
                        link = data["link"]
                        if not isinstance(body, str) or not isinstance(link, str):
                            raise ValueError("invalid cached response")
                        raw = body.encode("utf-8")
                        if len(raw) > MAX_BODY or len(link) > 3000:
                            raise ValueError("cached response exceeds bounds")
                        return raw, link
                    except (ValueError, KeyError, TypeError, UnicodeError,
                            RecursionError):
                        pass  # Stale/corrupt cache: fetch fresh public data.

        req = urllib.request.Request(url, headers={
            "User-Agent": "cezar-mojum-public-read-only/0.1",
            "Accept": "application/vnd.github+json",
        })
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                            urllib.request.HTTPSHandler(),
                                            NoRedirect())
        with client.open(req, timeout=14) as response:
            if response.status != 200 or response.geturl() != url:
                raise Refused("unexpected GitHub response or redirect")
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise Refused("GitHub response too large")
            link = response.headers.get("Link", "")[:3000]
        try:
            json.loads(raw)  # Reject non-JSON public responses.
        except RecursionError as exc:
            raise Refused("GitHub response JSON nesting exceeds limit") from exc
        data = {"body": raw.decode("utf-8"), "link": link}
        tempname = token + ".tmp." + str(os.getpid()) + "." + secrets.token_hex(12)
        tmp_fd = os.open(tempname, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         os.O_NOFOLLOW, 0o600, dir_fd=cache_fd)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as out:
                json.dump(data, out)
            os.replace(tempname, filename, src_dir_fd=cache_fd,
                       dst_dir_fd=cache_fd)
        finally:
            try:
                os.unlink(tempname, dir_fd=cache_fd)
            except FileNotFoundError:
                pass
        return raw, link
    finally:
        os.close(cache_fd)


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
        elif mode == "repo_plain":
            sys.stdout.write(REPO + "\n")
        elif mode == "graphql":
            sys.stdout.write(render_graphql(target) + "\n")
        elif mode == "graphql_checks":
            sys.stdout.write(render_checks(target) + "\n")
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
    except (Refused, PublicTabDenied, OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        print("Cezar public GitHub read denied/unavailable: "
              + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
