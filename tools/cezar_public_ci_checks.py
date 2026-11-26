#!/usr/bin/env python3
"""Bounded public GitHub CI glyph translation for the native Cezar PR panel.

The native Cezar 0.20.0 checks panel uses a fixed GraphQL PR aliases
read. This adapter never runs GraphQL. It reads only the pinned public
Mojum REST PR head list and each head's public check-runs, with a hard
per-hour API budget and private cache. An unknown result is NOT green.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import time
import urllib.request
import urllib.parse

from cezar_public_github_tab import NoRedirect, PublicTabDenied, _check_private_dir

REPO = "Garyu-tt/mojum"
BASE = "https://api.github.com/repos/" + REPO
CACHE_DIR = Path("/var/lib/cezar/home/public-gh-cache")
FILE = "cezar-ci-checks-v1.json"
LOCK = "cezar-ci-checks-v1.lock"
HEAD_TTL = 600
GLYPH_TTL = 1800
MAX_CHECK_FETCHES_PER_HOUR = 16
MAX_PER_QUERY = 8
MAX_BYTES = 2 * 1024 * 1024
MAX_ALIASES = 100
PRE = "query ($owner: String!, $name: String!) {\n  repository(owner: $owner, name: $name) {\n"
SUF = "\n  }\n}"
ALIAS = re.compile(r"    p([0-9]{1,2}): pullRequest\(number: ([1-9][0-9]{0,6})\) \{ commits\(last: 1\) \{ nodes \{ commit \{ statusCheckRollup \{ state \} \} \} \} \}")
SHA = re.compile("[0-9a-f]{40}\\Z")


def parse_checks(args):
    if (len(args) != 8 or args[:2] != ["api", "graphql"]
            or args[2] != "-f" or not args[3].startswith("query=")
            or args[4:] != ["-f", "owner=Garyu-tt", "-f", "name=mojum"]):
        raise PublicTabDenied("unsupported native CI-checks invocation")
    q = args[3][len("query="):]
    if len(q) > 15000 or not q.startswith(PRE) or not q.endswith(SUF):
        raise PublicTabDenied("CI query not the pinned read-only form")
    inside = q[len(PRE):-len(SUF)]
    lines = inside.split("\n")
    if not 1 <= len(lines) <= MAX_ALIASES:
        raise PublicTabDenied("CI request count outside bounds")
    numbers = []
    for i, line in enumerate(lines):
        match = ALIAS.fullmatch(line)
        if not match or match.group(1) != str(i):
            raise PublicTabDenied("unrecognized PR check alias")
        number = int(match.group(2))
        if number in numbers:
            raise PublicTabDenied("duplicate PR number")
        numbers.append(number)
    return numbers


def _read(url, client):
    if not url.startswith(BASE + "/"):
        raise PublicTabDenied("foreign GitHub repo")
    req = urllib.request.Request(url, headers={
        "User-Agent": "mojum-cezar-public-checks/1.0",
        "Accept": "application/vnd.github+json",
    })
    with client.open(req, timeout=5) as r:
        if r.status != 200 or r.geturl() != url:
            raise PublicTabDenied("unexpected GitHub CI resource")
        blob = r.read(MAX_BYTES + 1)
    if len(blob) > MAX_BYTES:
        raise PublicTabDenied("oversized public GitHub CI data")
    try:
        obj = json.loads(blob)
    except (ValueError, UnicodeError) as exc:
        raise PublicTabDenied("unreadable public CI data") from exc
    return obj


def _heads(client):
    url = BASE + "/pulls?" + urllib.parse.urlencode({
        "state": "open", "sort": "created", "direction": "desc", "per_page": 100,
    })
    rows = _read(url, client)
    if not isinstance(rows, list) or len(rows) > 100:
        raise PublicTabDenied("unsupported public PR list")
    heads = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PublicTabDenied("invalid public PR record")
        number = row.get("number")
        head = row.get("head")
        if type(number) is not int or not isinstance(head, dict):
            raise PublicTabDenied("invalid PR identity")
        sha = head.get("sha")
        if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
            raise PublicTabDenied("malformed head SHA")
        if row.get("state") != "open" or row.get("html_url") != f"https://github.com/{REPO}/pull/{number}":
            raise PublicTabDenied("unexpected PR resource")
        heads[str(number)] = sha
    return heads


def _check_state(sha, client):
    if not SHA.fullmatch(sha):
        raise PublicTabDenied("invalid check head")
    url = BASE + f"/commits/{sha}/check-runs?per_page=100"
    payload = _read(url, client)
    if not isinstance(payload, dict):
        raise PublicTabDenied("invalid check response")
    total = payload.get("total_count")
    entries = payload.get("check_runs")
    if (type(total) is not int or total < 0 or total > 100
            or not isinstance(entries, list) or len(entries) != total):
        # Missing pages or >100 checks cannot be called green.
        return None
    if not entries:
        return None
    seen_success = False
    seen_pending = False
    seen_failure = False
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        status, conclusion = entry.get("status"), entry.get("conclusion")
        if status not in ("queued", "in_progress", "completed"):
            return None
        if status != "completed" or conclusion is None:
            seen_pending = True
        elif conclusion in ("failure", "cancelled", "timed_out", "action_required",
                            "startup_failure"):
            seen_failure = True
        elif conclusion == "success":
            seen_success = True
        elif conclusion not in ("neutral", "skipped"):
            return None
    if seen_failure:
        return "FAILURE"
    if seen_pending:
        return "PENDING"
    return "SUCCESS" if seen_success else None


def _load_cache(path, now):
    try:
        st = os.lstat(path)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) & 0o077 or st.st_nlink != 1):
            raise PublicTabDenied("untrusted public CI cache")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("repo") != REPO
                or not isinstance(data.get("heads"), dict)
                or not isinstance(data.get("checks"), dict)
                or not isinstance(data.get("hour_used"), int)):
            raise ValueError("cache format")
        return data
    except FileNotFoundError:
        return {"repo": REPO, "heads": {}, "heads_at": 0, "checks": {},
                "hour_start": now, "hour_used": 0}
    except (OSError, ValueError, TypeError) as exc:
        raise PublicTabDenied("cache unreadable; fail closed") from exc


def _save(path, cache):
    raw = json.dumps(cache, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise PublicTabDenied("public CI cache size limit exceeded")
    tmp = path.with_name("." + path.name + f".tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def render_checks(numbers, *, client=None, cache_dir=CACHE_DIR, now=None):
    if not 1 <= len(numbers) <= MAX_ALIASES:
        raise PublicTabDenied("empty CI check request")
    now = time.time() if now is None else now
    _check_private_dir(cache_dir)
    client = client or urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  NoRedirect())
    lock_path = cache_dir / LOCK
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or stat.S_IMODE(st.st_mode) & 0o077 or st.st_nlink != 1):
            raise PublicTabDenied("untrusted public CI lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = cache_dir / FILE
        cache = _load_cache(path, now)
        if now - cache.get("hour_start", 0) > 3600 or now < cache.get("hour_start", 0):
            cache["hour_start"] = now
            cache["hour_used"] = 0
        if now - cache.get("heads_at", 0) > HEAD_TTL or now < cache.get("heads_at", 0):
            # Only one open-PR list every 10 minutes; no ambiguous statuses
            # if the public source is temporarily unreachable.
            cache["heads"] = _heads(client)
            cache["heads_at"] = now
        heads = cache["heads"]
        outputs = {}
        fetched_this_request = 0
        for number in numbers:
            sha = heads.get(str(number))
            state = None
            if isinstance(sha, str) and SHA.fullmatch(sha):
                old = cache["checks"].get(sha)
                if (isinstance(old, dict) and 0 <= now - old.get("at", -99999) < GLYPH_TTL):
                    state = old.get("state")
                elif (fetched_this_request < MAX_PER_QUERY
                      and cache["hour_used"] < MAX_CHECK_FETCHES_PER_HOUR):
                    # Count attempts before network access. Never bypass
                    # the hourly guard on 403, 429, timeouts or process restarts.
                    cache["hour_used"] += 1
                    fetched_this_request += 1
                    try:
                        state = _check_state(sha, client)
                    except (OSError, ValueError, PublicTabDenied):
                        state = None
                    cache["checks"][sha] = {"at": now, "state": state}
            outputs[str(number)] = state
        # Trim stale SHA entries so this owner-private file cannot grow forever.
        cache["checks"] = {
            k: v for k, v in cache["checks"].items()
            if isinstance(v, dict) and isinstance(v.get("at"), (int, float))
            and 0 <= now - v["at"] < 86400
        }
        _save(path, cache)
        # Cezar accepts missing rollup (null) as "unknown", never passing.
        repository = {}
        for index, number in enumerate(numbers):
            state = outputs[str(number)]
            repository[f"p{index}"] = {"commits": {"nodes": [
                {"commit": {"statusCheckRollup": {"state": state} if state else None}}
            ]}}
        return json.dumps({"data": {"repository": repository}}, separators=(",", ":"))
    finally:
        os.close(fd)
