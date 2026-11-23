"""Offline admission contract for PRIVATE archive encryption profiles.

This module validates declarations only. It does not encrypt, decrypt, load keys,
open files, or authenticate the caller's claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

SCHEMA = "mojum.archive-encryption-profile/1"
_ALLOWED_CIPHERS = {"AES-256-GCM", "ChaCha20-Poly1305"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KEY_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SEGMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_REQUIRED = frozenset({
    "schema", "classification", "enabled", "cipher", "key_ref",
    "key_version", "nonce_bytes", "tag_bytes", "segment_id",
    "plaintext_sha256", "ciphertext_sha256", "aad_schema",
})


class ArchiveEncryptionProfileError(ValueError):
    """Raised when a declared encryption profile is not admissible."""


@dataclass(frozen=True)
class ArchiveEncryptionProfile:
    schema: str
    classification: str
    enabled: bool
    cipher: str
    key_ref: str
    key_version: int
    nonce_bytes: int
    tag_bytes: int
    segment_id: str
    plaintext_sha256: str
    ciphertext_sha256: str
    aad_schema: str
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "classification": self.classification,
            "enabled": self.enabled, "cipher": self.cipher,
            "key_ref": self.key_ref, "key_version": self.key_version,
            "nonce_bytes": self.nonce_bytes, "tag_bytes": self.tag_bytes,
            "segment_id": self.segment_id,
            "plaintext_sha256": self.plaintext_sha256,
            "ciphertext_sha256": self.ciphertext_sha256,
            "aad_schema": self.aad_schema, "digest": self.digest,
        }


def _fail(message: str) -> None:
    raise ArchiveEncryptionProfileError(message)


def _canonical_digest(values: dict[str, Any]) -> str:
    encoded = json.dumps(
        values, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def admit_profile(raw: Mapping[str, Any], *, private_opt_in: bool = False) -> ArchiveEncryptionProfile:
    """Validate and detach a caller-declared PRIVATE archive encryption profile."""
    if type(raw) is not dict:
        _fail("profile must be an exact built-in dict")
    if type(private_opt_in) is not bool or not private_opt_in:
        _fail("explicit private_opt_in=True is required")
    # Bound attacker-controlled key strings before cardinality checks and
    # frozenset hashing/comparison, so oversized hostile keys fail closed.
    if any(type(key) is not str or len(key) > 32 for key in raw):
        _fail("profile keys must be exact native strings of at most 32 characters")
    if len(raw) != len(_REQUIRED):
        _fail("profile fields must match the exact schema")
    if frozenset(raw.keys()) != _REQUIRED:
        _fail("profile fields must match the exact schema")

    values = dict(raw)
    # Check primitive types before comparisons or regex operations: hostile
    # caller objects must never get an opportunity to run custom equality.
    string_fields = (
        "schema", "classification", "cipher", "key_ref", "segment_id",
        "plaintext_sha256", "ciphertext_sha256", "aad_schema",
    )
    if any(type(values[name]) is not str for name in string_fields):
        _fail("all textual profile fields must be exact strings")
    string_limits = {
        "schema": 64, "classification": 16, "cipher": 32, "key_ref": 128,
        "segment_id": 96, "plaintext_sha256": 64, "ciphertext_sha256": 64,
        "aad_schema": 64,
    }
    if any(len(values[name]) > limit for name, limit in string_limits.items()):
        _fail("textual profile field exceeds its length budget")
    if type(values["enabled"]) is not bool or values["enabled"] is not True:
        _fail("encryption must be explicitly enabled")
    for name in ("key_version", "nonce_bytes", "tag_bytes"):
        if type(values[name]) is not int:
            _fail(f"{name} must be an exact native integer")

    if values["schema"] != SCHEMA:
        _fail("unsupported profile schema")
    if values["classification"] != "PRIVATE":
        _fail("only PRIVATE archive profiles are admitted")
    if values["cipher"] not in _ALLOWED_CIPHERS:
        _fail("cipher is not in the reviewed algorithm allowlist")
    if not _KEY_REF.fullmatch(values["key_ref"]):
        _fail("key_ref must be a bounded opaque identifier, not key material or a path")
    if not 1 <= values["key_version"] <= 2**31 - 1:
        _fail("key_version must be a positive bounded native integer")
    if values["nonce_bytes"] != 12:
        _fail("nonce_bytes must be exactly 12 for the admitted suites")
    if values["tag_bytes"] != 16:
        _fail("tag_bytes must be exactly 16 for the admitted suites")
    if not _SEGMENT_ID.fullmatch(values["segment_id"]):
        _fail("segment_id must be a bounded identifier")
    for name in ("plaintext_sha256", "ciphertext_sha256"):
        if not _HEX64.fullmatch(values[name]):
            _fail(f"{name} must be a lowercase SHA-256 hex digest")
    if values["aad_schema"] != "mojum.archive-aad/1":
        _fail("unsupported associated-data schema")

    digest = _canonical_digest(values)
    return ArchiveEncryptionProfile(
        schema=values["schema"], classification=values["classification"],
        enabled=values["enabled"], cipher=values["cipher"],
        key_ref=values["key_ref"], key_version=values["key_version"],
        nonce_bytes=values["nonce_bytes"], tag_bytes=values["tag_bytes"],
        segment_id=values["segment_id"],
        plaintext_sha256=values["plaintext_sha256"],
        ciphertext_sha256=values["ciphertext_sha256"],
        aad_schema=values["aad_schema"], digest=digest,
    )


def verify_profile(profile: ArchiveEncryptionProfile) -> bool:
    """Verify detached profile consistency; this is not cryptographic authentication."""
    if type(profile) is not ArchiveEncryptionProfile:
        _fail("profile must be an ArchiveEncryptionProfile")
    values = profile.as_dict()
    digest = values.pop("digest")
    if type(digest) is not str or len(digest) != 64 or not _HEX64.fullmatch(digest):
        _fail("profile digest is malformed")
    admitted = admit_profile(values, private_opt_in=True)
    return admitted.digest == digest
