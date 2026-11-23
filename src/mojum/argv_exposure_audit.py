"""Offline, redacted advisory audit of synthetic command-line arguments.

No process, environment, filesystem, network or shell access.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

_SCHEMA = "mojum.argv-exposure/1"
_SENSITIVE = re.compile(r"(?:^|[-_])(password|passwd|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credential|auth)(?:$|[-_])", re.I)
_URL_USERINFO = re.compile(r"(?i)://[^/\\s@]+:[^/\\s@]+@")
_MAX_ARGS = 256
_MAX_LENGTH = 4096
_MAX_TOTAL = 65536


@dataclass(frozen=True)
class Finding:
    index: int
    kind: str


@dataclass(frozen=True)
class AuditResult:
    schema: str
    argument_count: int
    findings: tuple[Finding, ...]
    digest: str

    @property
    def safe(self) -> bool:
        return not self.findings


def audit_argv(argv: list[str], *, provenance: str = "PUBLIC_SYNTHETIC") -> AuditResult:
    """Return redacted findings; never include argument text or its hash.

    Hash is of sanitized findings only, not of potentially secret-bearing input.
    """
    if type(provenance) is not str or provenance != "PUBLIC_SYNTHETIC":
        raise ValueError("explicit PUBLIC_SYNTHETIC provenance required")
    if type(argv) is not list or len(argv) > _MAX_ARGS:
        raise ValueError("argv must be a bounded native list")
    total = 0
    findings: list[Finding] = []
    pending_secret_value = False
    for index, arg in enumerate(argv):
        if type(arg) is not str:
            raise ValueError("arguments must be native strings")
        if not arg or len(arg) > _MAX_LENGTH or "\\x00" in arg:
            raise ValueError("empty, oversized or NUL-containing argument")
        try:
            encoded_length = len(arg.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("argument is not valid UTF-8 text") from exc
        total += encoded_length
        if total > _MAX_TOTAL:
            raise ValueError("argv exceeds byte budget")

        if pending_secret_value:
            findings.append(Finding(index, "sensitive-option-value"))
            pending_secret_value = False
        elif arg.startswith("--"):
            option, separator, _ = arg[2:].partition("=")
            if _SENSITIVE.search(option):
                findings.append(Finding(index, "sensitive-option"))
                if not separator:
                    pending_secret_value = True
        elif arg.startswith("-") and len(arg) > 1:
            option, separator, _ = arg[1:].partition("=")
            if _SENSITIVE.search(option):
                findings.append(Finding(index, "sensitive-option"))
                if not separator:
                    pending_secret_value = True

        # Inspect every argument independently of option-name parsing. URL
        # credentials can be embedded in --endpoint=..., -u=..., or a value
        # consumed by a preceding sensitive option.
        if _URL_USERINFO.search(arg):
            findings.append(Finding(index, "url-userinfo"))

    if pending_secret_value:
        findings.append(Finding(len(argv) - 1, "missing-sensitive-option-value"))
    findings.sort(key=lambda item: (item.index, item.kind))
    canonical = json.dumps({"schema": _SCHEMA, "count": len(argv),
                            "findings": [[f.index, f.kind] for f in findings]},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return AuditResult(_SCHEMA, len(argv), tuple(findings),
                       hashlib.sha256(canonical.encode("ascii")).hexdigest())
