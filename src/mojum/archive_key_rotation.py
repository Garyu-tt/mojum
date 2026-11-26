"""Offline verification of an owner-authorized archive signing-key rotation chain.

This module verifies detached metadata only. It never reads keys from disk, changes
keys, accesses archives, or authenticates a live device.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence

SCHEMA = "mojum.archive-key-rotation/1"
_MAX_ROTATIONS = 32
_KEY_BYTES = 32
_HEX_256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_RECORD_FIELDS = frozenset({
    "schema", "epoch", "previous_fingerprint", "next_fingerprint", "signature"
})


class RotationError(ValueError):
    """Raised when a rotation chain is malformed or cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class RotationReport:
    schema: str
    initial_epoch: int
    final_epoch: int
    rotation_count: int
    initial_fingerprint: str
    final_fingerprint: str
    chain_sha256: str


def _is_hex_256(value: object) -> bool:
    """Admit bounded native SHA-256 hex before invoking the regex engine."""
    return type(value) is str and len(value) == 64 and _HEX_256.fullmatch(value) is not None


def key_fingerprint(key: bytes) -> str:
    """Return the SHA-256 fingerprint of an exact native key byte string."""
    if type(key) is not bytes or len(key) < _KEY_BYTES or len(key) > 4096:
        raise RotationError("key must be 32..4096 native bytes")
    return hashlib.sha256(key).hexdigest()


def _rotation_message(epoch: int, previous: str, following: str) -> bytes:
    # Fixed field order and separators are the canonical signed representation.
    return (
        '{"schema":"' + SCHEMA + '","epoch":' + str(epoch)
        + ',"previous":"' + previous + '","next":"' + following + '"}'
    ).encode("ascii")


def make_rotation(epoch: int, current_key: bytes, next_key: bytes) -> dict[str, object]:
    """Build a detached rotation record; caller must protect both key materials."""
    if type(epoch) is not int or epoch < 1 or epoch > 2**31 - 1:
        raise RotationError("epoch must be a positive bounded native integer")
    previous = key_fingerprint(current_key)
    following = key_fingerprint(next_key)
    if hmac.compare_digest(previous, following):
        raise RotationError("next key must differ from current key")
    signature = hmac.new(current_key, _rotation_message(epoch, previous, following), hashlib.sha256).hexdigest()
    return {
        "schema": SCHEMA,
        "epoch": epoch,
        "previous_fingerprint": previous,
        "next_fingerprint": following,
        "signature": signature,
    }


def verify_rotation_chain(
    initial_key: bytes,
    initial_epoch: int,
    rotations: Sequence[Mapping[str, object]],
    keyring: Mapping[str, bytes],
    expected_final_fingerprint: str,
) -> RotationReport:
    """Verify a bounded, contiguous chain against an independently pinned final key.

    keyring maps fingerprints to intermediate signing keys for verification only.
    The initial key is supplied separately; no key bytes are returned.
    """
    if type(initial_epoch) is not int or not 0 <= initial_epoch <= 2**31 - 1:
        raise RotationError("initial_epoch must be a bounded native integer")
    if type(rotations) not in (list, tuple) or len(rotations) > _MAX_ROTATIONS:
        raise RotationError("rotations must be a list/tuple of at most 32 records")
    if type(keyring) is not dict or len(keyring) > _MAX_ROTATIONS:
        raise RotationError("keyring must be a native dict of at most 32 entries")
    if not _is_hex_256(expected_final_fingerprint):
        raise RotationError("expected final fingerprint must be lowercase SHA-256 hex")

    # Admit all keyring entries before using any key for hashing/equality/lookup.
    # Exact built-in strings/bytes prevent caller-defined methods from running.
    admitted_keyring: dict[str, bytes] = {}
    for fingerprint, key in keyring.items():
        if not _is_hex_256(fingerprint):
            raise RotationError("keyring fingerprints must be native lowercase SHA-256 hex")
        if type(key) is not bytes:
            raise RotationError("keyring values must be native bytes")
        if not hmac.compare_digest(key_fingerprint(key), fingerprint):
            raise RotationError("keyring key does not match its fingerprint")
        admitted_keyring[fingerprint] = key

    initial_fp = key_fingerprint(initial_key)
    current_fp = initial_fp
    current_epoch = initial_epoch
    seen = {initial_fp}
    chain = hashlib.sha256()
    for record in rotations:
        if type(record) is not dict or len(record) != len(_RECORD_FIELDS):
            raise RotationError("rotation record has an invalid exact shape")
        # Bound cardinality before materializing keys, and reject subclasses
        # before set/hash/equality operations can invoke caller code.
        keys = tuple(record)
        if any(type(key) is not str or len(key) > 32 for key in keys):
            raise RotationError("rotation record keys must be bounded native strings")
        if set(keys) != _RECORD_FIELDS:
            raise RotationError("rotation record has an invalid exact shape")
        schema = record["schema"]
        epoch = record["epoch"]
        previous = record["previous_fingerprint"]
        following = record["next_fingerprint"]
        signature = record["signature"]
        if type(schema) is not str or len(schema) != len(SCHEMA) or schema != SCHEMA:
            raise RotationError("unsupported rotation schema")
        if type(epoch) is not int or epoch != current_epoch + 1 or epoch > 2**31 - 1:
            raise RotationError("rotation epochs must be contiguous and increasing")
        if not _is_hex_256(previous) or previous != current_fp:
            raise RotationError("previous key fingerprint does not match chain head")
        if not _is_hex_256(following) or following == current_fp:
            raise RotationError("invalid next key fingerprint")
        if following in seen:
            raise RotationError("key rotation cycle or rollback detected")
        if not _is_hex_256(signature):
            raise RotationError("invalid rotation signature encoding")

        signing_key = initial_key if current_fp == initial_fp else admitted_keyring.get(current_fp)
        if type(signing_key) is not bytes or not hmac.compare_digest(key_fingerprint(signing_key), current_fp):
            raise RotationError("missing or mismatched intermediate signing key")
        expected = hmac.new(signing_key, _rotation_message(epoch, previous, following), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise RotationError("rotation authorization signature mismatch")

        chain.update(_rotation_message(epoch, previous, following))
        chain.update(bytes.fromhex(signature))
        current_fp = following
        current_epoch = epoch
        seen.add(following)

    if not hmac.compare_digest(current_fp, expected_final_fingerprint):
        raise RotationError("terminal key does not match independently pinned fingerprint")
    return RotationReport(
        schema=SCHEMA,
        initial_epoch=initial_epoch,
        final_epoch=current_epoch,
        rotation_count=len(rotations),
        initial_fingerprint=initial_fp,
        final_fingerprint=current_fp,
        chain_sha256=chain.hexdigest(),
    )
