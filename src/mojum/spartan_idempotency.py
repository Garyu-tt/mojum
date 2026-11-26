"""Deterministic non-secret idempotency identifiers for Spartan provider calls.

This does not guarantee provider-side deduplication; caller must send and enforce
provider-specific idempotency semantics separately.
"""
import hashlib
import json
import re

_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}", re.ASCII)


def provider_request_key(*, issue: int, base_sha: str, patch_sha: str, provider: str, model: str, operation: str = "generate") -> str:
    """Return a stable opaque key, never incorporating prompts, tokens or secrets."""
    if type(issue) is not int or not 1 <= issue <= 2**31 - 1:
        raise ValueError("invalid issue")
    if type(base_sha) is not str or not _SHA.fullmatch(base_sha):
        raise ValueError("invalid base SHA")
    if type(patch_sha) is not str or not _SHA.fullmatch(patch_sha):
        raise ValueError("invalid patch SHA")
    for value in (provider, model, operation):
        if type(value) is not str or not _MODEL.fullmatch(value):
            raise ValueError("invalid identifier")
    payload = json.dumps(["mojum-spartan-provider-idempotency-v1", issue, base_sha, patch_sha, provider, model, operation], separators=(",", ":"), ensure_ascii=True)
    return "sp1_" + hashlib.sha256(payload.encode("ascii")).hexdigest()[:48]
