"""Offline exact-head CI gate for PR acceptance (issue #415).

The merge endpoint is not proof that the PR-triggered workflows finished, and
parallel automation can poll a stale rollup. This module turns a *snapshot* of
a pull request plus the SHA the caller intends to merge into a deterministic
verdict:

* ``ready``  -- every required check completed successfully for that exact
  head SHA, the base matches, and GitHub reports a clean merge state;
* ``defer``  -- checks are queued/running/missing, the PR is a draft, or the
  merge state is not fresh enough to decide (re-query later);
* ``reject`` -- a required check failed/was cancelled/timed out/was skipped
  without a waiver, or the head/base/conflict state makes the merge unsafe.

The module never calls a merge endpoint and never mutates a repository. A
caller is expected to run it immediately before merging and to pass the same
SHA to ``gh pr merge --match-head-commit`` so a last-second push cannot slip
past the verdict.

Inputs are the JSON produced by ``gh pr view --json ...`` (camelCase) and a
small snake_case fixture schema used by the tests. No third-party packages and
no network access are required when ``--input`` is used.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, Mapping, Sequence

SCHEMA = "mojum.merge-gate.v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024

EXIT_READY = 0
EXIT_REJECT = 1
EXIT_DEFER = 2
EXIT_USAGE = 64

READY = "ready"
DEFER = "defer"
REJECT = "reject"

PENDING_STATES = frozenset(
    {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}
)
PASSING_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL"})
WAIVABLE_CONCLUSIONS = frozenset({"SKIPPED"})
FAILING_CONCLUSIONS = frozenset(
    {
        "FAILURE",
        "CANCELLED",
        "TIMED_OUT",
        "ACTION_REQUIRED",
        "STALE",
        "ERROR",
        "STARTUP_FAILURE",
    }
)

_SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_GH_FIELDS = (
    "number,headRefOid,baseRefName,headRefName,isDraft,mergeable,"
    "mergeStateStatus,statusCheckRollup"
)


class SnapshotError(ValueError):
    """The supplied PR snapshot does not have the expected shape."""


class FetchError(RuntimeError):
    """``gh pr view`` could not produce a snapshot."""


@dataclass(frozen=True)
class CheckOutcome:
    """One entry of a PR's check rollup, normalized to lower-case tokens."""

    name: str
    status: str
    conclusion: str | None = None
    workflow: str | None = None
    head_sha: str | None = None

    @property
    def label(self) -> str:
        if self.workflow:
            return f"{self.workflow} / {self.name}"
        return self.name


@dataclass(frozen=True)
class PullRequestFacts:
    head_sha: str
    base_ref: str
    checks: tuple[CheckOutcome, ...] = ()
    number: int | None = None
    head_ref: str | None = None
    is_draft: bool = False
    mergeable: str = "UNKNOWN"
    merge_state_status: str | None = None


@dataclass(frozen=True)
class GateReason:
    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class GateResult:
    verdict: str
    code: str
    reasons: tuple[GateReason, ...] = ()
    required: tuple[str, ...] = ()
    matched: tuple[str, ...] = field(default_factory=tuple)

    @property
    def exit_code(self) -> int:
        return {READY: EXIT_READY, DEFER: EXIT_DEFER}.get(self.verdict, EXIT_REJECT)


def _text(raw: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_check(raw: object, index: int) -> CheckOutcome:
    if not isinstance(raw, Mapping):
        raise SnapshotError(f"check[{index}] must be an object")

    name = _text(raw, "name", "context")
    if not name:
        raise SnapshotError(f"check[{index}] has no name or context")

    workflow = _text(raw, "workflowName", "workflow")
    head_sha = raw.get("headSha", raw.get("head_sha"))
    if head_sha is not None and (
        not isinstance(head_sha, str) or _SHA_RE.fullmatch(head_sha) is None
    ):
        raise SnapshotError(f"check[{index}] head SHA must be a full hexadecimal Git object ID")
    typename = raw.get("__typename")
    status_raw = _text(raw, "status")
    conclusion_raw = _text(raw, "conclusion")
    state_raw = _text(raw, "state")

    if state_raw is not None:
        # StatusContext: EXPECTED/PENDING are non-terminal; SUCCESS/FAILURE/
        # ERROR are terminal legacy commit statuses.
        state = state_raw.upper()
        if state in {"FAILURE", "ERROR"}:
            conclusion_raw = state
        if status_raw is None:
            if state in {"SUCCESS", "FAILURE", "ERROR"}:
                status_raw = "COMPLETED"
                conclusion_raw = conclusion_raw or state
            else:
                status_raw = state

    if status_raw is None:
        if conclusion_raw is not None:
            status_raw = "COMPLETED"
        else:
            raise SnapshotError(f"check[{index}] has neither status nor conclusion")

    status = status_raw.upper()
    if status == "COMPLETED":
        status = "COMPLETED"
    elif status in PENDING_STATES:
        status = "PENDING"
    else:
        status = "UNKNOWN"

    conclusion = conclusion_raw.upper() if conclusion_raw else None
    return CheckOutcome(
        name=name,
        status=status,
        conclusion=conclusion,
        workflow=workflow,
        head_sha=head_sha,
    )


def parse_snapshot(raw: object) -> PullRequestFacts:
    """Accept ``gh pr view --json`` output or the snake_case fixture schema."""

    if not isinstance(raw, Mapping):
        raise SnapshotError("snapshot must be a JSON object")

    head_sha = raw.get("headRefOid", raw.get("head_sha", raw.get("headSha")))
    if head_sha is None:
        raise SnapshotError("snapshot has no head SHA")
    if not isinstance(head_sha, str) or _SHA_RE.fullmatch(head_sha) is None:
        raise SnapshotError("snapshot head SHA must be a full hexadecimal Git object ID")

    base_ref = _text(raw, "baseRefName", "base_ref", "baseRef")
    if not base_ref:
        raise SnapshotError("snapshot has no base ref")

    rollup = raw.get("statusCheckRollup", raw.get("checks", []))
    if rollup is None:
        rollup = []
    if not isinstance(rollup, Sequence) or isinstance(rollup, (str, bytes)):
        raise SnapshotError("statusCheckRollup must be a list")
    checks = tuple(_parse_check(item, index) for index, item in enumerate(rollup))

    is_draft_raw = raw.get("isDraft", raw.get("is_draft", raw.get("draft", False)))
    if not isinstance(is_draft_raw, bool):
        raise SnapshotError("isDraft must be a boolean")

    mergeable = _text(raw, "mergeable") or "UNKNOWN"
    merge_state = _text(raw, "mergeStateStatus", "merge_state_status")
    number = raw.get("number")
    if number is not None and (not isinstance(number, int) or isinstance(number, bool)):
        raise SnapshotError("number must be an integer")

    return PullRequestFacts(
        head_sha=head_sha,
        base_ref=base_ref,
        checks=checks,
        number=number,
        head_ref=_text(raw, "headRefName", "head_ref", "headRef"),
        is_draft=is_draft_raw,
        mergeable=mergeable.upper(),
        merge_state_status=merge_state.upper() if merge_state else None,
    )


def _matches(pattern: str, check: CheckOutcome) -> bool:
    # Never certify an incomplete matrix through an aggregate workflow name
    # or a wildcard. Every expected job must be enumerated by the caller.
    if " / " in pattern:
        return check.workflow is not None and pattern == check.label
    return pattern == check.name


def evaluate(
    facts: PullRequestFacts,
    *,
    expected_head_sha: str | None,
    expected_base: str = "main",
    required: Sequence[str] = (),
    waivers: Mapping[str, str] | None = None,
) -> GateResult:
    """Return the gate verdict for an already-parsed snapshot."""

    if expected_head_sha is not None and (
        not isinstance(expected_head_sha, str) or _SHA_RE.fullmatch(expected_head_sha) is None
    ):
        raise ValueError("expected_head_sha must be a full 40- or 64-character hexadecimal Git object ID")

    waivers = dict(waivers or {})
    rejects: list[GateReason] = []
    defers: list[GateReason] = []
    patterns = tuple(required)
    matched_labels: list[str] = []

    if not expected_head_sha:
        rejects.append(
            GateReason(
                "EXPECTED_HEAD_REQUIRED",
                "pass the exact head SHA that will be merged; a rollup alone is stale",
            )
        )
    elif facts.head_sha.lower() != expected_head_sha.lower():
        rejects.append(
            GateReason(
                "HEAD_MISMATCH",
                f"snapshot head {facts.head_sha} is not the expected {expected_head_sha}",
            )
        )

    if expected_base and facts.base_ref != expected_base:
        rejects.append(
            GateReason(
                "BASE_MISMATCH",
                f"PR targets {facts.base_ref!r}, expected {expected_base!r}",
            )
        )

    if facts.is_draft:
        defers.append(GateReason("DRAFT", "draft PRs are never merge candidates"))

    if facts.mergeable == "CONFLICTING":
        rejects.append(GateReason("CONFLICTING", "GitHub reports the PR is conflicting"))
    elif facts.mergeable != "MERGEABLE":
        defers.append(
            GateReason(
                "MERGEABLE_UNKNOWN",
                f"mergeability is {facts.mergeable!r}; re-query before deciding",
            )
        )

    state = facts.merge_state_status
    if state is None:
        defers.append(
            GateReason("MERGE_STATE_UNKNOWN", "mergeStateStatus is absent")
        )
    elif state != "CLEAN":
        defers.append(
            GateReason(
                "MERGE_STATE_NOT_CLEAN",
                f"mergeStateStatus is {state!r}; required reviews/checks may be pending",
            )
        )

    if not patterns:
        rejects.append(
            GateReason(
                "NO_REQUIRED_CHECKS",
                "no required check patterns were supplied; a gate without requirements proves nothing",
            )
        )

    for pattern in patterns:
        # A rollup does not list jobs which have not registered yet. A
        # wildcard (or workflow-only selector) cannot prove matrix completeness.
        # Fail closed unless each required *job* is named exactly.
        if (not isinstance(pattern, str) or not pattern.strip()
                or any(meta in pattern for meta in "*?[]")):
            rejects.append(GateReason(
                "AMBIGUOUS_REQUIRED_PATTERN",
                f"required job {pattern!r} must be an exact job name or workflow / job label",
            ))
            continue
        matches = [check for check in facts.checks if _matches(pattern, check)]
        if not matches:
            defers.append(
                GateReason(
                    "MISSING_REQUIRED_CHECK",
                    f"no check matched required pattern {pattern!r}",
                )
            )
            continue

        for check in matches:
            matched_labels.append(check.label)
            if check.head_sha and check.head_sha.lower() != facts.head_sha.lower():
                defers.append(
                    GateReason(
                        "STALE_CHECK",
                        f"{check.label} ran for head {check.head_sha}, not {facts.head_sha}",
                    )
                )
                continue

            # A failed conclusion is authoritative even if a partially
            # refreshed rollup still reports PENDING or an unknown status.
            if check.conclusion in FAILING_CONCLUSIONS:
                rejects.append(
                    GateReason(
                        "CHECK_FAILED",
                        f"{check.label} concluded {check.conclusion}",
                    )
                )
                continue

            if check.status == "PENDING":
                defers.append(
                    GateReason(
                        "CHECK_PENDING",
                        f"{check.label} is not complete (status {check.status})",
                    )
                )
                continue
            if check.status != "COMPLETED":
                defers.append(
                    GateReason(
                        "CHECK_UNKNOWN_STATUS",
                        f"{check.label} has an unrecognized status {check.status!r}",
                    )
                )
                continue
            if check.conclusion in PASSING_CONCLUSIONS:
                continue
            if check.conclusion in WAIVABLE_CONCLUSIONS:
                if pattern in waivers:
                    continue
                rejects.append(
                    GateReason(
                        "CHECK_SKIPPED_UNWAIVED",
                        f"{check.label} was skipped without an explicit waiver",
                    )
                )
                continue
            rejects.append(
                GateReason(
                    "CHECK_UNKNOWN_CONCLUSION",
                    f"{check.label} has an unrecognized conclusion {check.conclusion!r}",
                )
            )

    reasons = tuple(rejects + defers)
    if rejects:
        verdict, code = REJECT, rejects[0].code
    elif defers:
        verdict, code = DEFER, defers[0].code
    else:
        verdict, code = READY, "READY"
    return GateResult(
        verdict=verdict,
        code=code,
        reasons=reasons,
        required=patterns,
        matched=tuple(dict.fromkeys(matched_labels)),
    )


def fetch_snapshot(pr: int, *, gh: str = "gh") -> dict[str, object]:
    """Read a PR snapshot through the GitHub CLI (network; opt-in only)."""

    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise ValueError("PR number must be a positive integer")
    command = [gh, "pr", "view", str(pr), "--json", _GH_FIELDS]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise FetchError(f"cannot run {' '.join(command)}: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FetchError(f"gh pr view exited {completed.returncode}: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise FetchError(f"gh pr view returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise FetchError("gh pr view did not return a JSON object")
    return payload


def _load_snapshot(path: str) -> object:
    if path == "-":
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        data = stream.read(MAX_INPUT_BYTES + 1)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if len(data) > MAX_INPUT_BYTES:
            raise SnapshotError(f"stdin exceeds {MAX_INPUT_BYTES} bytes")
        return json.loads(data.decode("utf-8"))

    source = Path(path)
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise SnapshotError(f"{path} exceeds {MAX_INPUT_BYTES} bytes")
    return json.loads(source.read_text(encoding="utf-8"))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via CLI test
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _parse_waivers(values: Iterable[str]) -> dict[str, str]:
    waivers: dict[str, str] = {}
    for value in values:
        pattern, separator, reason = value.partition("=")
        if not separator or not pattern.strip() or not reason.strip():
            raise ValueError(f"--waive expects PATTERN=REASON, got {value!r}")
        waivers[pattern.strip()] = reason.strip()
    return waivers


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="mojum-merge-gate",
        description=(
            "Offline exact-head CI gate. Prints a READY/DEFER/REJECT verdict and "
            "never performs a merge."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="PR snapshot JSON file, or - for stdin")
    source.add_argument("--pr", type=int, help="PR number to read via gh (network)")
    parser.add_argument(
        "--expected-head-sha",
        required=True,
        help="the exact commit SHA the caller intends to merge",
    )
    parser.add_argument("--expected-base", default="main", help="expected base ref")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "one exact required job name or 'workflow / job' label; repeat "
            "for every expected matrix job (wildcards and workflow-only "
            "selectors are not allowed)"
        ),
    )
    parser.add_argument(
        "--waive",
        action="append",
        default=[],
        metavar="PATTERN=REASON",
        help="accept a skipped check matching PATTERN, recording REASON",
    )
    parser.add_argument("--gh", default="gh", help="GitHub CLI executable for --pr")
    return parser


def _emit(payload: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_signal:
        if exit_signal.code in (EXIT_USAGE, 0):
            return int(exit_signal.code)
        raise

    try:
        waivers = _parse_waivers(args.waive)
        if args.pr is not None:
            snapshot = fetch_snapshot(args.pr, gh=args.gh)
        else:
            snapshot = _load_snapshot(args.input)
        facts = parse_snapshot(snapshot)
        result = evaluate(
            facts,
            expected_head_sha=args.expected_head_sha,
            expected_base=args.expected_base,
            required=args.require,
            waivers=waivers,
        )
    except (SnapshotError, FetchError, ValueError, OSError, json.JSONDecodeError) as error:
        _emit(
            {
                "schema": SCHEMA,
                "verdict": REJECT,
                "code": "INVALID_INPUT",
                "exit_code": EXIT_REJECT,
                "error": str(error),
                "merge_performed": False,
            }
        )
        return EXIT_REJECT

    _emit(
        {
            "schema": SCHEMA,
            "verdict": result.verdict,
            "code": result.code,
            "exit_code": result.exit_code,
            "number": facts.number,
            "head_sha": facts.head_sha,
            "expected_head_sha": args.expected_head_sha,
            "base_ref": facts.base_ref,
            "expected_base": args.expected_base,
            "required": list(result.required),
            "matched": list(result.matched),
            "waivers": waivers,
            "reasons": [reason.as_dict() for reason in result.reasons],
            "merge_performed": False,
        }
    )
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
