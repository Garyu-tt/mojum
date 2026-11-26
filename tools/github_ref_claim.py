#!/usr/bin/env python3
"""Opt-in atomic GitHub ref reservation for Mojum issue subscopes.

The *create ref* operation is the sole ownership decision. Issue comments are
best-effort announcements and cannot substitute for GitHub's unique ref check.
No third-party packages, host mutations, or live journal access.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib import error, request

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_SCOPE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
BASE = "main"
MAX_SCOPE = 64


class ClaimError(Exception):
    """Expected, safe-to-display reservation failure."""


class ClaimTaken(ClaimError):
    """The exact Git ref already exists; no ownership was acquired."""


class ApiError(ClaimError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"GitHub request refused (HTTP {status})")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ref_name(issue: int, scope: str) -> str:
    """Deterministic scope key. Identical issue+scope cannot create twice."""
    if type(issue) is not int or not 1 <= issue <= 999_999_999:
        raise ClaimError("issue must be an integer between 1 and 999999999")
    if type(scope) is not str or not 1 <= len(scope) <= MAX_SCOPE or not _SCOPE.fullmatch(scope):
        raise ClaimError("scope must be a lowercase ASCII hyphenated slug (1..64 chars)")
    return f"refs/heads/claims/issue-{issue}/{scope}"


def repo_name(repo: str) -> str:
    if type(repo) is not str or not _REPO.fullmatch(repo):
        raise ClaimError("repo must be owner/name with ASCII components")
    for component in repo.split("/"):
        if component in {".", ".."} or ".." in component:
            raise ClaimError("repo has an invalid component")
    return repo


class GitHubAPI:
    """Small closed GitHub REST surface. Never include request body in errors."""

    def __init__(self, token: str):
        if type(token) is not str or not token.strip():
            raise ClaimError("GITHUB_TOKEN is required only for --reserve")
        self._token = token
        # Do not silently follow redirects or send credentials via env proxies.
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())

    def _request(self, method: str, repo: str, path: str, payload: dict | None = None) -> dict:
        repo_name(repo)
        if not path.startswith("/") or "?" in path or ".." in path:
            raise ClaimError("unsafe API path")
        url = f"https://api.github.com/repos/{repo}{path}"
        body = None if payload is None else json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        req = request.Request(url, data=body, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self._token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mojum-ref-claim/1",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        try:
            with self._opener.open(req, timeout=10) as resp:
                if resp.status not in (200, 201):
                    raise ApiError(resp.status)
                raw = resp.read(128 * 1024 + 1)
        except error.HTTPError as exc:
            raise ApiError(exc.code) from None
        except (error.URLError, TimeoutError, OSError):
            raise ClaimError("GitHub connection failure; outcome may be unknown") from None
        if len(raw) > 128 * 1024:
            raise ClaimError("GitHub response exceeded the safety budget")
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ClaimError("GitHub returned invalid JSON") from None
        if type(obj) is not dict:
            raise ClaimError("GitHub returned an invalid object")
        return obj

    def issue_is_open(self, repo: str, issue: int) -> bool:
        obj = self._request("GET", repo, f"/issues/{issue}")
        return (type(obj.get("number")) is int and obj["number"] == issue
                and obj.get("state") == "open" and "pull_request" not in obj)

    def ref_sha(self, repo: str, ref: str) -> str | None:
        suffix = ref.removeprefix("refs/")
        try:
            obj = self._request("GET", repo, f"/git/ref/{suffix}")
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise
        sha = obj.get("object", {}).get("sha") if type(obj.get("object")) is dict else None
        if obj.get("ref") != ref or type(sha) is not str or not _SHA.fullmatch(sha):
            raise ClaimError("GitHub returned an invalid ref")
        return sha

    def create_ref(self, repo: str, ref: str, sha: str) -> None:
        obj = self._request("POST", repo, "/git/refs", {"ref": ref, "sha": sha})
        value = obj.get("object")
        if obj.get("ref") != ref or type(value) is not dict or value.get("sha") != sha:
            raise ClaimError("GitHub create-ref reply ambiguous; inspect remote ref manually")

    def announce(self, repo: str, issue: int, scope: str, ref: str) -> None:
        branch = ref.removeprefix("refs/heads/")
        body = (f"🔒 **CLAIMED via atomic Git ref**: '{scope}' (issue #{issue}).\n\n"
                f"Reservation branch: '{branch}'. This notice is advisory; "
                "the unique Git ref is the actual reservation. "
                "Do not reuse this scope until explicitly released.")
        self._request("POST", repo, f"/issues/{issue}/comments", {"body": body})


def reserve(api: GitHubAPI, repo: str, issue: int, scope: str) -> dict:
    """Reserve *once*, then announce; never overwrite an existing ref."""
    repo_name(repo)
    ref = ref_name(issue, scope)
    if not api.issue_is_open(repo, issue):
        raise ClaimError("target must be an open GitHub issue, not a PR")
    if api.ref_sha(repo, ref) is not None:
        raise ClaimTaken("scope already reserved; choose another issue/scope")
    base_sha = api.ref_sha(repo, f"refs/heads/{BASE}")
    if base_sha is None:
        raise ClaimError("main branch is missing")
    try:
        api.create_ref(repo, ref, base_sha)
    except ApiError as exc:
        # A 422 can mean a collision *or* invalid ref/policy: distinguish.
        if exc.status == 422 and api.ref_sha(repo, ref) is not None:
            raise ClaimTaken("scope was reserved by a competing worker") from None
        raise
    # A connection failure may happen *after* GitHub creates the ref.
    # Do not auto-retry a non-idempotent POST or infer owner from a readback.
    result = {"status": "reserved", "repo": repo, "issue": issue,
              "scope": scope, "ref": ref, "base_sha": base_sha}
    try:
        api.announce(repo, issue, scope, ref)
    except ClaimError:
        result["status"] = "reserved_comment_failed"
        result["warning"] = "claim ref exists; issue comment failed; verify manually"
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="owner/repo")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--scope", required=True, help="unique lowercase slug for the scope")
    p.add_argument("--reserve", action="store_true", help="perform authenticated remote writes")
    args = p.parse_args(argv)
    try:
        repo = repo_name(args.repo)
        ref = ref_name(args.issue, args.scope)
        if not args.reserve:
            result = {"status": "dry_run", "repo": repo, "issue": args.issue,
                      "scope": args.scope, "ref": ref}
        else:
            api = GitHubAPI(os.environ.get("GITHUB_TOKEN", ""))
            result = reserve(api, repo, args.issue, args.scope)
    except ClaimTaken as exc:
        print(json.dumps({"status": "taken", "error": str(exc)}, sort_keys=True))
        return 3
    except ClaimError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
