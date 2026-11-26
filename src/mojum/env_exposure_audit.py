"""Offline, name-only environment exposure preflight (no os.environ access)."""
from __future__ import annotations

import re
from collections.abc import Iterable

MAX_NAMES = 1024
MAX_NAME_LENGTH = 128
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE = re.compile(
    r"(?:^|_)(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIALS|AUTH_KEY)(?:_|$)"
)


def audit_environment_names(names: Iterable[str]) -> dict:
    """Return a deterministic, JSON-serializable name-only diagnostic report.

    Reject unbounded or malformed inputs rather than inspecting environment values.
    """
    if isinstance(names, (str, bytes, dict)):
        raise TypeError("names must be an iterable of strings")
    try:
        iterator = iter(names)
    except TypeError as exc:
        raise TypeError("names must be iterable") from exc
    seen_folded: dict[str, str] = {}
    seen_exact: set[str] = set()
    findings: list[dict[str, str]] = []
    count = 0
    for name in iterator:
        count += 1
        if count > MAX_NAMES:
            raise ValueError("too many environment names")
        if type(name) is not str:
            raise TypeError("environment names must be strings")
        if len(name) > MAX_NAME_LENGTH or not _NAME.fullmatch(name):
            raise ValueError("invalid environment variable name")
        folded = name.upper()
        if name in seen_exact:
            findings.append({"name": name, "code": "duplicate"})
        elif folded in seen_folded:
            findings.append({"name": name, "code": "case_collision"})
        else:
            seen_folded[folded] = name
        seen_exact.add(name)
        if _SENSITIVE.search(folded):
            findings.append({"name": name, "code": "sensitive_name"})
    findings.sort(key=lambda item: (item["name"], item["code"]))
    return {"schema": "mojum.env-exposure-audit/1", "count": count,
            "ok": not findings, "findings": findings}
