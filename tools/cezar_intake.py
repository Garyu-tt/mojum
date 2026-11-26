#!/usr/bin/env python3
"""Single trusted GitHub label intake for the cross-rule Cezar admission gate.

Never directly enables Cezar native event automations. Public metadata only,
no GitHub tokens or PR rights. Model tasks are read-only and sent through
cezar_admission's atomic cross-rule key, maximum one unresolved task.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import urllib.request

try:
    from . import cezar_admission as admission
except ImportError:
    import cezar_admission as admission

REPO = "Garyu-tt/mojum"
BASE = f"https://api.github.com/repos/{REPO}"
TRIGGER = "cezar-ready"
EXCLUDE = frozenset({"cezar-hold", "security", "needs-human"})
MAX_ITEMS = 20
MAX_API_BYTES = 320_000
_SHA = re.compile(r"[a-f0-9]{40}\Z")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects before urllib contacts a new destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise admission.AdmissionError("GitHub API redirect forbidden")


def fetch_json(url: str, expected: type, *, opener=None):
    if not url.startswith(BASE + "/") and not url.startswith(BASE + "?"):
        raise admission.AdmissionError("unexpected public GitHub API path")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "cezar-mojum-bounded-label-intake/1.0",
    })
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with client.open(req, timeout=12) as response:
        if response.status != 200 or response.geturl() != url:
            raise admission.AdmissionError("unexpected GitHub origin/status")
        raw = response.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise admission.AdmissionError("oversized public GitHub response")
    try:
        data = json.loads(raw, object_pairs_hook=admission._strict_pairs,
                          parse_constant=admission._reject_constant)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise admission.AdmissionError("invalid public GitHub JSON") from exc
    if not isinstance(data, expected):
        raise admission.AdmissionError("unexpected public GitHub response type")
    return data


def discover(*, opener=None) -> list[dict]:
    url = BASE + "/issues?state=open&labels=cezar-ready&per_page=20"
    entries = fetch_json(url, list, opener=opener)
    if len(entries) > MAX_ITEMS:
        raise admission.AdmissionError("more issues than the bounded first page")
    result = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise admission.AdmissionError("invalid issue record")
        number = entry.get("number")
        if type(number) is not int or not 1 <= number <= 10_000_000:
            raise admission.AdmissionError("invalid public issue number")
        if number in seen:
            raise admission.AdmissionError("duplicate issue number")
        seen.add(number)
        if entry.get("state") != "open":
            continue
        label_records = entry.get("labels", [])
        if not isinstance(label_records, list) or len(label_records) > 64:
            raise admission.AdmissionError("malformed labels")
        names = set()
        for label in label_records:
            if not isinstance(label, dict) or not isinstance(label.get("name"), str):
                raise admission.AdmissionError("malformed label")
            names.add(label["name"])
        if TRIGGER not in names or names & EXCLUDE:
            continue
        kind = "pull" if "pull_request" in entry else "issue"
        expected_url = f"https://github.com/{REPO}/{'pull' if kind == 'pull' else 'issues'}/{number}"
        if entry.get("html_url") != expected_url:
            raise admission.AdmissionError("unexpected GitHub resource URL")
        if kind == "pull":
            detail = fetch_json(BASE + f"/pulls/{number}", dict, opener=opener)
            head = detail.get("head")
            if (not isinstance(head, dict) or not isinstance(head.get("sha"), str)
                    or _SHA.fullmatch(head["sha"]) is None):
                raise admission.AdmissionError("missing PR head sha")
            revision = head["sha"]
        else:
            revision = entry.get("updated_at")
            if not isinstance(revision, str):
                raise admission.AdmissionError("missing issue revision")
        scope = f"cezar/review/{kind}/{number}"
        ident = admission.identity(REPO, kind, number, revision, "review", scope)
        # Title is metadata, never prompt authority. Bound and remove control chars.
        title = entry.get("title")
        if not isinstance(title, str) or len(title) > 800:
            raise admission.AdmissionError("invalid issue title")
        title = "".join(x if x.isprintable() else " " for x in title)[:300]
        result.append({"identity": ident, "title": title, "url": expected_url})
    return result


def pending(db):
    # No second task while an earlier Cezar run is unresolved. This is a
    # deliberate CPU safety ceiling, not a 300-inference concurrency claim.
    return db.execute("SELECT count(*) FROM admission WHERE status IN ('claimed','submitted')").fetchone()[0]


def make_prompt(record: dict) -> str:
    ident = record["identity"]
    return (
        f"Read-only first-pass Mojum {ident['kind']} #{ident['number']}.\n"
        f"Source: {record['url']}\n"
        f"Revision: {ident['revision']}\n"
        f"Untrusted title: {record['title']}\n"
        "Do not edit files, run commands, create issues/PRs, approve, merge or "
        "claim that tests passed. Provide <=120 words: one concrete code "
        "inspection question and a proposed exact-path test. If you cannot "
        "identify files from available evidence, say so. No guessed facts."
    )


def one_poll(db, prompts_dir: Path, *, execute=False, opener=None):
    records = discover(opener=opener)
    summary = {"schema": "mojum.cezar.intake.v1", "discovered": len(records),
               "eligible": 0, "dispatched": 0, "duplicates": 0, "deferred": 0,
               "read_only": True}
    if not records:
        return summary
    for r in records:
        ident = r["identity"]
        exists = db.execute("SELECT status FROM admission WHERE key=?", (ident["key"],)).fetchone()
        if exists is not None:
            summary["duplicates"] += 1
            continue
        summary["eligible"] += 1
        if not execute or pending(db):
            summary["deferred"] += 1
            continue
        admission._validate_dir(prompts_dir)
        prompt = make_prompt(r)
        raw = prompt.encode("utf-8")
        if len(raw) > admission.MAX_PROMPT:
            raise admission.AdmissionError("prompt exceeds admitted budget")
        filename = ident["key"] + ".txt"
        p = prompts_dir / filename
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            # Another poller may have already prepared this same revision.
            # Never overwrite its prompt or mistake a local file for a new run.
            summary["deferred"] += 1
            continue
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            p.unlink(missing_ok=True)
            raise
        dispatched = admission.dispatch(db, ident, p)
        if dispatched["admitted"]:
            summary["dispatched"] += 1
        else:
            # A concurrent poller won the atomic single-flight claim, or the
            # revision was already claimed. Do not retain an unused prompt.
            p.unlink(missing_ok=True)
            if dispatched.get("reason") == "capacity-busy":
                summary["deferred"] += 1
            else:
                summary["duplicates"] += 1
        break
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--prompts-dir", required=True, type=Path)
    ap.add_argument("--execute", action="store_true",
                    help="explicit opt-in for a maximum of one read-only task")
    args = ap.parse_args(argv)
    old = os.umask(0o077)
    try:
        with closing(admission.connect(args.db)) as db:
            summary = one_poll(db, args.prompts_dir, execute=args.execute)
    except (admission.AdmissionError, OSError, sqlite3.DatabaseError) as exc:
        ap.exit(2, "intake refused: " + type(exc).__name__ + "\n")
    finally:
        os.umask(old)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
