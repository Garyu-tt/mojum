#!/usr/bin/env python3
"""Read-only, fail-closed intake for PRIVATE Spartan model proposal artifacts.

Output is metadata ONLY. A successful Git diff is still untrusted code:
no patch application, test execution, GitHub publishing or merge happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from tools import spartan_patch_guard as patch_guard

MAX_ARTIFACT_BYTES = 512 * 1024
SCHEMA = "mojum.spartan.local-proposal.v1"


class IntakeError(ValueError):
    """Proposal cannot be trusted as a bounded private review artifact."""


def _strict_pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise IntakeError("duplicate JSON keys")
        data[key] = value
    return data


def _bad_constant(_):
    raise IntakeError("nonfinite JSON value")


def _private_directory_fd(directory: Path):
    if (not directory.is_absolute() or
            os.path.realpath(directory) != os.path.abspath(directory)):
        raise IntakeError("noncanonical or symlinked private directory")
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(directory, flags)
        info = os.fstat(fd)
    except OSError as exc:
        raise IntakeError("private directory unavailable") from exc
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        os.close(fd)
        raise IntakeError("private directory must be owner-only")
    return fd


def _read_artifact(directory, task_id, attempt):
    if not isinstance(task_id, str) or not 1 <= len(task_id) <= 256:
        raise IntakeError("invalid expected task identity")
    if type(attempt) is not int or attempt not in (1, 2, 3):
        raise IntakeError("invalid expected lease attempt")
    filename = (hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]
                + f"-attempt-{attempt}.json")
    folder_fd = _private_directory_fd(Path(directory))
    artifact_fd = -1
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            artifact_fd = os.open(filename, flags, dir_fd=folder_fd)
            info = os.fstat(artifact_fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_nlink != 1):
                raise IntakeError("artifact must be owner-only regular file")
            with os.fdopen(artifact_fd, "rb") as stream:
                artifact_fd = -1
                raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        except OSError as exc:
            raise IntakeError("private artifact unavailable") from exc
    finally:
        if artifact_fd >= 0:
            os.close(artifact_fd)
        os.close(folder_fd)
    if not raw or len(raw) > MAX_ARTIFACT_BYTES:
        raise IntakeError("artifact byte budget exceeded")
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs,
                            parse_constant=_bad_constant)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise IntakeError("artifact is not strict JSON") from exc
    return parsed, hashlib.sha256(raw).hexdigest()


def assess_private_artifact(private_dir, *, task_id, scope, attempt, allowed_paths):
    """Reject untrusted provenance; inspect the exact, unmodified model response.

    The operator supplies the trusted task ID, scope and exact path grants.
    Neither paths nor the target scope may be read from model-authored text.
    """
    if (not isinstance(scope, str) or not 1 <= len(scope) <= 256
            or not isinstance(allowed_paths, (list, tuple)) or not allowed_paths):
        raise IntakeError("expected scope and exact path grants required")
    data, artifact_sha256 = _read_artifact(private_dir, task_id, attempt)
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA
            or data.get("task_id") != task_id or data.get("scope") != scope
            or type(data.get("attempt")) is not int
            or data["attempt"] != attempt
            or data.get("reviewed") is not False
            or data.get("patch_applied") is not False
            or data.get("tests_run") is not False):
        raise IntakeError("artifact does not match pending review lease")
    calls = data.get("model_calls")
    if not isinstance(calls, list) or not 1 <= len(calls) <= 2:
        raise IntakeError("invalid model invocation count")
    assessments = []
    for index, call in enumerate(calls):
        if (not isinstance(call, dict)
                or not isinstance(call.get("proposal"), str)
                or not isinstance(call.get("model"), str)
                or not 1 <= len(call["model"]) <= 80
                or type(call.get("port")) is not int
                or not 1 <= call["port"] <= 65535):
            raise IntakeError("invalid model invocation metadata")
        proposal = call["proposal"]
        record = {"invocation": index, "patch_ready_for_next_gate": False}
        if not proposal.startswith("diff --git a/"):
            record["reason"] = "not_an_exact_git_diff"
        else:
            try:
                summary = patch_guard.validate_patch(proposal, allowed_paths)
            except patch_guard.PatchGuardError:
                record["reason"] = "git_diff_rejected_by_scope_or_syntax_guard"
            else:
                record.update({
                    "patch_ready_for_next_gate": True,
                    "patch_sha256": summary["sha256"],
                    "file_count": summary["file_count"],
                    "hunks": summary["hunks"],
                    "additions": summary["additions"],
                    "deletions": summary["deletions"],
                })
        assessments.append(record)
    ready = sum(x["patch_ready_for_next_gate"] for x in assessments)
    return {
        "schema": "mojum.spartan.patch-intake-report.v1",
        "task_id": task_id,
        "scope": scope,
        "attempt": attempt,
        "private_artifact_sha256": artifact_sha256,
        "model_calls_assessed": len(assessments),
        "syntax_and_scope_candidates": ready,
        "not_patch_candidates": len(assessments) - ready,
        "candidates": assessments,
        "only_read_only_preflight": True,
        "safe_to_execute": False,
        "tests_run": False,
        "github_published": False,
        "accepted_by_independent_qa": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--allow-path", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        result = assess_private_artifact(
            args.private_dir, task_id=args.task_id, scope=args.scope,
            attempt=args.attempt, allowed_paths=args.allow_path)
    except (IntakeError, OSError, ValueError) as exc:
        # Never reveal model text or raw private paths in diagnostics.
        print(json.dumps({"ok": False, "reason": "private_intake_rejected",
                          "error_type": type(exc).__name__}),
              file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
