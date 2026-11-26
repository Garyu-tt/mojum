#!/usr/bin/env python3
"""Offline-safe REST bridge for Cezar 0.20.0's read-only GitHub tab.

Cezar's native tab asks `gh api graphql` for one fixed, OPEN-only list.
The host's real `gh` has no auth. This module recognizes only that
list shape and renders actual public GitHub REST data into the exact
read-only shape expected by the tab. It never executes GraphQL requests,
exports credentials, writes to GitHub or invents PR checks/diff metrics.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import time
import urllib.request
import urllib.parse

REPO = "Garyu-tt/mojum"
HOST = "https://api.github.com"
CACHE_DIR = Path("/var/lib/cezar/home/public-gh-cache")
CACHE_NAME = "mojum-open-for-cezar-tab-v1.json"
CACHE_TTL = 180
MAX_PAGE = 4
MAX_RAW = 5 * 1024 * 1024
MAX_ARG = 8000


class PublicTabDenied(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicTabDenied("redirect denied")


def parse_graphql(args):
    """Accept only Cezar's bounded open-issues-and-PRs GraphQL LIST query."""
    if len(args) < 4 or args[:2] != ["api", "graphql"] or len(args) % 2:
        raise PublicTabDenied("unsupported gh API command")
    parameters = {}
    for i in range(2, len(args), 2):
        flag, item = args[i], args[i+1]
        if flag not in ("-f", "-F") or not isinstance(item, str) or "=" not in item:
            raise PublicTabDenied("unexpected gh argument")
        name, value = item.split("=", 1)
        if name in parameters or len(value) > MAX_ARG:
            raise PublicTabDenied("duplicate or oversized GraphQL parameter")
        if name not in ("query", "owner", "name", "issuesLimit", "prsLimit",
                        "issuesCursor", "prsCursor"):
            raise PublicTabDenied("GraphQL parameter not allowlisted")
        if (name in ("issuesLimit", "prsLimit")) != (flag == "-F"):
            raise PublicTabDenied("incorrect variable type")
        parameters[name] = value
    if parameters.get("owner") != "Garyu-tt" or parameters.get("name") != "mojum":
        raise PublicTabDenied("repository mismatch")
    q = parameters.get("query", "")
    # Do not let an arbitrary GraphQL document request private or mutable
    # information. We do NOT execute q; we only map the exact public list.
    required = (
        "query ($owner: String!, $name: String!, $issuesLimit: Int!, $prsLimit: Int!",
        "issues(first: $issuesLimit, after: $issuesCursor, states: OPEN",
        "pullRequests(first: $prsLimit, after: $prsCursor, states: OPEN",
        "orderBy: {field: CREATED_AT, direction: DESC}",
        "totalCount", "pageInfo", "isDraft",
    )
    if not all(s in q for s in required):
        raise PublicTabDenied("unsupported GraphQL document")
    if re.search(r"\b(mutation|subscription|__schema|__type)\b", q, re.I):
        raise PublicTabDenied("non-list GraphQL document")
    if sum(v in q for v in ("issues(first:", "pullRequests(first:")) != 2:
        raise PublicTabDenied("unexpected list query")
    for key in ("issuesLimit", "prsLimit"):
        val = parameters.get(key)
        if val is None or not val.isascii() or not val.isdecimal() or len(val) > 3:
            raise PublicTabDenied("invalid requested page size")
        num = int(val)
        if not 0 <= num <= 100:
            raise PublicTabDenied("page size outside supported limits")
        parameters[key] = num
    for key, prefix in (("issuesCursor", "i:"), ("prsCursor", "p:")):
        value = parameters.get(key)
        if value is None:
            parameters[key] = 0
        else:
            if not value.startswith(prefix) or not value[len(prefix):].isascii() or not value[len(prefix):].isdecimal():
                raise PublicTabDenied("invalid opaque pagination cursor")
            offset = int(value[len(prefix):])
            if not 1 <= offset <= MAX_PAGE * 100:
                raise PublicTabDenied("cursor outside snapshot")
            parameters[key] = offset
    parameters.pop("query")
    return parameters


def _check_private_dir(dirpath):
    if not dirpath.is_absolute() or os.path.realpath(dirpath) != str(dirpath):
        raise PublicTabDenied("noncanonical cache directory")
    dirpath.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(dirpath)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) & 0o077):
        raise PublicTabDenied("cache directory not private")


def _fetch(url, opener):
    if not url.startswith(HOST + "/repos/" + REPO + "/issues?"):
        raise PublicTabDenied("GitHub REST URL not allowlisted")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "mojum-cezar-public-tab-read/1.0",
    })
    with opener.open(req, timeout=13) as resp:
        if resp.status != 200 or resp.geturl() != url:
            raise PublicTabDenied("GitHub returned an unexpected resource")
        raw = resp.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW:
        raise PublicTabDenied("REST issue list too large")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise PublicTabDenied("malformed GitHub REST JSON") from exc
    if not isinstance(value, list):
        raise PublicTabDenied("GitHub REST issue listing is not an array")
    return value


def _normalise(entry):
    if not isinstance(entry, dict):
        raise PublicTabDenied("invalid GitHub issue/PR record")
    number = entry.get("number")
    if type(number) is not int or not 1 <= number <= 10_000_000:
        raise PublicTabDenied("invalid public issue number")
    kind = "pr" if isinstance(entry.get("pull_request"), dict) else "issue"
    expected_url = f"https://github.com/{REPO}/{'pull' if kind == 'pr' else 'issues'}/{number}"
    if entry.get("html_url") != expected_url:
        raise PublicTabDenied("GitHub resource identity mismatch")
    title = entry.get("title")
    created = entry.get("created_at")
    if (not isinstance(title, str) or len(title) > 4096
            or not isinstance(created, str) or len(created) > 50):
        raise PublicTabDenied("unusable GitHub display record")
    author = entry.get("user")
    if author is not None and not isinstance(author, dict):
        raise PublicTabDenied("malformed GitHub author")
    login = author.get("login") if author else None
    if login is not None and (not isinstance(login, str) or len(login) > 100):
        raise PublicTabDenied("malformed GitHub author login")
    label_items = entry.get("labels")
    if not isinstance(label_items, list) or len(label_items) > 100:
        raise PublicTabDenied("malformed GitHub labels")
    labels = []
    for label in label_items[:20]:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str):
            raise PublicTabDenied("malformed GitHub label name")
        color = label.get("color", "")
        if not isinstance(color, str):
            raise PublicTabDenied("malformed GitHub label color")
        labels.append({"name": label["name"][:100], "color": color[:6]})
    comments = entry.get("comments")
    if type(comments) is not int or not 0 <= comments <= 10_000_000:
        raise PublicTabDenied("unknown GitHub comment count")
    result = {
        "number": number,
        "title": title,
        "author": {"login": login} if login else None,
        "createdAt": created,
        "url": expected_url,
        "labels": {"nodes": labels},
        "comments": {"totalCount": comments},
    }
    if kind == "pr":
        flag = entry.get("draft")
        if type(flag) is not bool:
            raise PublicTabDenied("GitHub PR draft state unknown")
        result["isDraft"] = flag
        # No REST list-level additions/deletions. Omit, do not fabricate.
    return kind, result


def collect(*, opener=None):
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    issues, prs, seen = [], [], set()
    for page in range(1, MAX_PAGE + 1):
        query = urllib.parse.urlencode({
            "state": "open", "per_page": 100, "sort": "created",
            "direction": "desc", "page": page,
        })
        rows = _fetch(HOST + "/repos/" + REPO + "/issues?" + query, client)
        for row in rows:
            number = row.get("number") if isinstance(row, dict) else None
            if number in seen:
                raise PublicTabDenied("GitHub duplicate issue in open-list pages")
            seen.add(number)
            kind, item = _normalise(row)
            (prs if kind == "pr" else issues).append(item)
        if len(rows) < 100:
            break
        if page == MAX_PAGE:
            raise PublicTabDenied("open list exceeds bounded pagination")
    return {"issues": issues, "prs": prs}


def snapshot(*, opener=None, cache_dir=CACHE_DIR, now=None):
    now = now if now is not None else time.time()
    _check_private_dir(cache_dir)
    p = cache_dir / CACHE_NAME
    if p.exists():
        st = os.lstat(p)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) & 0o077 or st.st_nlink != 1):
            raise PublicTabDenied("unsafe cached GitHub snapshot")
        if 0 <= now - st.st_mtime < CACHE_TTL:
            try:
                cached = json.loads(p.read_text(encoding="utf-8"))
                if (cached.get("repo") == REPO and isinstance(cached.get("issues"), list)
                        and isinstance(cached.get("prs"), list)):
                    return cached
            except (OSError, UnicodeError, ValueError, AttributeError):
                pass
    result = {"repo": REPO, **collect(opener=opener)}
    blob = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(blob) > MAX_RAW:
        raise PublicTabDenied("public snapshot unexpectedly large")
    tmp = cache_dir / ("." + CACHE_NAME + ".tmp." + str(os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return result


def to_page(data, params):
    if not isinstance(data, dict) or data.get("repo") != REPO:
        raise PublicTabDenied("snapshot for an unexpected repository")
    def one(kind, size, cursor):
        items = data["issues" if kind == "i" else "prs"]
        if not isinstance(items, list):
            raise PublicTabDenied("malformed cached snapshot")
        if not 0 <= cursor <= len(items):
            raise PublicTabDenied("cursor outside current snapshot")
        selected = items[cursor:cursor + size]
        end = cursor + len(selected)
        more = end < len(items) and size > 0
        return {
            "totalCount": len(items),
            "nodes": selected,
            "pageInfo": {"hasNextPage": more, "endCursor": f"{kind}:{end}" if more else None},
        }
    return {"data": {"repository": {
        "issues": one("i", params["issuesLimit"], params["issuesCursor"]),
        "pullRequests": one("p", params["prsLimit"], params["prsCursor"]),
    }}}


def render_graphql(parameters, *, opener=None, cache_dir=CACHE_DIR, now=None):
    return json.dumps(to_page(snapshot(opener=opener, cache_dir=cache_dir, now=now),
                              parameters), ensure_ascii=False, separators=(",", ":"))
