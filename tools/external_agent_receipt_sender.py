"""Offline-only signed external agent receipt producer for Mojum issue #717.

Takes a fully specified observation event on standard input and emits one
HMAC-signed JSON envelope. No event is manufactured from a schedule, a PR, or
an agent reservation; callers must independently attest the observed work.
No networking, persistent sender state, or credential echoing.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

from mojum.external_agent_heartbeat import (
    MAX_RECEIPT_BYTES,
    ReceiptError,
    sign_receipt,
    validate_receipt,
)

MAX_KEY_BYTES = 4096


class SenderError(ValueError):
    """Refused input or local key-file configuration."""


def read_private_signing_key(path: str | os.PathLike[str]) -> bytes:
    """Read a private *raw binary* key, never a CLI/env-provided secret.

    POSIX only: this helper intentionally refuses Windows rather than
    inaccurately claiming to have verified its file ACLs.
    """
    if os.name != "posix" or not hasattr(os, "geteuid"):
        raise SenderError("private key ACL verification requires POSIX")
    if not isinstance(path, (str, os.PathLike)):
        raise SenderError("invalid key file path")
    target = Path(path)
    if not target.is_absolute():
        raise SenderError("key file path must be absolute")

    try:
        before = target.lstat()
    except OSError as exc:
        raise SenderError("cannot stat private key file") from exc
    if (not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in (0o400, 0o600)
            or before.st_uid != os.geteuid()
            or not 32 <= before.st_size <= MAX_KEY_BYTES):
        raise SenderError("private key must be owner-only, single-link, 32-4096 bytes")

    # O_NOFOLLOW is a necessary condition for atomic key-file admission.
    if not hasattr(os, "O_NOFOLLOW"):
        raise SenderError("O_NOFOLLOW is required for private key admission")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = None
    try:
        fd = os.open(target, flags)
        after = os.fstat(fd)
        if (not stat.S_ISREG(after.st_mode)
                or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or after.st_nlink != 1
                or stat.S_IMODE(after.st_mode) not in (0o400, 0o600)
                or after.st_uid != os.geteuid()
                or not 32 <= after.st_size <= MAX_KEY_BYTES):
            raise SenderError("private key file changed or is unsafe")
        key = bytearray()
        while len(key) <= MAX_KEY_BYTES:
            part = os.read(fd, MAX_KEY_BYTES + 1 - len(key))
            if not part:
                break
            key.extend(part)
        if not 32 <= len(key) <= MAX_KEY_BYTES or len(key) != after.st_size:
            raise SenderError("invalid private key length")
        return bytes(key)
    except OSError as exc:
        raise SenderError("cannot read private key file") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for name, item in pairs:
        if name in value:
            raise SenderError("duplicate JSON field")
        value[name] = item
    return value


def _reject_constant(_value: str) -> None:
    raise SenderError("nonfinite JSON constant")


def signed_packet(raw_event: bytes, signing_key: bytes) -> bytes:
    """Validate strict JSON once, sign with canonical wire semantics.

    Returns a one-line ASCII packet: {"event": {...}, "signature": "<hex>"}.
    It is a transport *payload*, not a transport or claim of observed activity.
    """
    if type(raw_event) is not bytes or not 1 <= len(raw_event) <= MAX_RECEIPT_BYTES:
        raise SenderError("receipt input is empty or too large")
    try:
        event = json.loads(
            raw_event.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SenderError("invalid UTF-8 JSON receipt") from exc
    try:
        canonical = validate_receipt(event)
        signature = sign_receipt(canonical, signing_key)
    except ReceiptError as exc:
        raise SenderError("receipt schema or signature key rejected") from exc
    envelope = {"event": canonical, "signature": signature}
    return (json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline signed receipt producer; stdin JSON -> stdout signed JSON"
    )
    parser.add_argument("--key-file", required=True,
                        help="absolute path to a POSIX owner-only binary HMAC key")
    args = parser.parse_args(argv)
    try:
        # Never expose a key as an argument, environment variable, or JSON field.
        key = read_private_signing_key(args.key_file)
        raw = sys.stdin.buffer.read(MAX_RECEIPT_BYTES + 1)
        packet = signed_packet(raw, key)
    except (SenderError, OSError) as exc:
        # Errors are fixed, non-sensitive messages; no raw event, path or key.
        print(f"receipt producer refused: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
