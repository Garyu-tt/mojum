"""Opt-in, single-winner GitHub issue reservation via atomic Git ref creation.

This command never mutates existing Git refs or repository issue metadata. It
only attempts POST /git/refs and, upon conflict, GET /git/ref/heads/....
The remote reservation is advisory, not a durable lease or worker identity.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

_API = "https://api.github.com"
_MAX_REPLY = 8192
_VERSION = "2022-11-28"
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z", re.ASCII)
_MAX_ISSUE = 2**31 - 1


class ClaimError(ValueError):
    """Ambiguous input, GitHub rejection or network error (no token echoed)."""


@dataclass(frozen=True)
class ClaimResult:
    acquired: bool
    ref: str
    head_sha: str


class _RejectRedirect(HTTPRedirectHandler):
    """Fail closed: never forward the token to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate(repo: str, issue: int, sha: str, token: str) -> tuple[str, str]:
    if type(repo) is not str or repo.count("/") != 1:
        raise ClaimError("expected owner/repo")
    owner, name = repo.split("/")
    if (any(_SLUG.fullmatch(s) is None or s in (".", "..") for s in (owner, name))
            or name.endswith(".git")):
        raise ClaimError("invalid owner/repo")
    if type(issue) is not int or not 1 <= issue <= _MAX_ISSUE:
        raise ClaimError("invalid issue number")
    if type(sha) is not str or _SHA.fullmatch(sha) is None:
        raise ClaimError("expected an exact 40-hex base commit")
    if (type(token) is not str or not 1 <= len(token) <= 2048
            or any(not 33 <= ord(c) <= 126 for c in token)):
        raise ClaimError("GITHUB_TOKEN is missing or invalid")
    return repo, sha.lower()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ClaimError("ambiguous duplicate GitHub response key")
        value[key] = item
    return value


def _decode_response(response):
    raw = response.read(_MAX_REPLY + 1)
    if len(raw) > _MAX_REPLY:
        raise ClaimError("oversized GitHub response")
    try:
        doc = json.loads(raw, object_pairs_hook=_unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ClaimError("invalid JSON constant")))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ClaimError("invalid GitHub JSON response") from exc
    if type(doc) is not dict:
        raise ClaimError("unexpected GitHub response shape")
    return doc


def _verify_ref(response_doc, ref):
    if (response_doc.get("ref") != ref
            or type(response_doc.get("object")) is not dict
            or response_doc["object"].get("type") != "commit"):
        raise ClaimError("GitHub ref response mismatch")
    sha = response_doc["object"].get("sha")
    if type(sha) is not str or _SHA.fullmatch(sha) is None:
        raise ClaimError("invalid GitHub ref target")
    return sha.lower()


def _request(opener, method, url, token, body=None):
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _VERSION,
        "User-Agent": "mojum-atomic-issue-claim/1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(request, timeout=10) as response:
            status = response.status
            if status != (201 if method == "POST" else 200):
                raise ClaimError("unexpected GitHub HTTP status")
            return _decode_response(response)
    except HTTPError:
        raise
    except (URLError, TimeoutError, OSError) as exc:
        # Error messages from urllib may include proxy or request details.
        raise ClaimError("GitHub transport failed") from exc


def reserve_issue_ref(repo: str, issue: int, sha: str, token: str, *, opener=None) -> ClaimResult:
    """Atomically attempt one claim, with read-only conflict confirmation.

    HTTP 422 is not automatically a competing claim: verify the exact ref via
    GET before reporting BUSY. All other errors fail closed.
    """
    repo, sha = _validate(repo, issue, sha, token)
    ref = f"refs/heads/agent-claims/issue-{issue}"
    base = f"{_API}/repos/{repo}/git"
    if opener is None:
        opener = build_opener(_RejectRedirect())
    body = json.dumps({"ref": ref, "sha": sha}, separators=(",", ":")).encode("ascii")
    try:
        created = _request(opener, "POST", base + "/refs", token, body)
    except HTTPError as exc:
        if exc.code != 422:
            raise ClaimError(f"GitHub refused claim (HTTP {exc.code})") from None
        try:
            existing = _request(opener, "GET", base + f"/ref/heads/agent-claims/issue-{issue}", token)
        except HTTPError as read_exc:
            raise ClaimError(f"claim conflict not verified (HTTP {read_exc.code})") from None
        return ClaimResult(False, ref, _verify_ref(existing, ref))
    actual = _verify_ref(created, ref)
    if actual != sha:
        raise ClaimError("new ref points to unexpected commit")
    return ClaimResult(True, ref, actual)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub repository owner/name")
    parser.add_argument("--issue", required=True, type=int, help="Existing issue ID to reserve")
    parser.add_argument("--sha", required=True, help="Pinned 40-hex base commit (not a moving ref)")
    args = parser.parse_args(argv)
    try:
        result = reserve_issue_ref(args.repo, args.issue, args.sha,
                                   os.environ.get("GITHUB_TOKEN", ""))
    except ClaimError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result.acquired:
        print(f"ACQUIRED {result.ref} {result.head_sha}")
        return 0
    print(f"BUSY {result.ref} {result.head_sha}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
