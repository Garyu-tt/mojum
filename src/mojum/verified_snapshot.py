"""Opt-in HMAC-protected local Aquarium replay accelerator (stdlib only).

The replay cache is PRIVATE plaintext: HMAC authenticates its contents to an
explicit separate 32-byte secret but DOES NOT encrypt the data or prevent
rollback if an attacker controls both the DB and the snapshot. The canonical
SQLite Noosphere is the durable journal, never the sidecar snapshot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile

SCHEMA = "mojum.aquarium.hmac-replay-snapshot/1"
STATE_SCHEMA = "mojum.aquarium.replay-state/1"
STATE_SCHEMA_V4 = "mojum.aquarium.replay-state/2"  # explicit epoch-index state
MAX_SNAPSHOT_BYTES = 1_048_576
HEX = frozenset("0123456789abcdef")


class SnapshotError(ValueError):
    """Missing/invalid key, snapshot, format or local filesystem protections."""


def canonical(value):
    try:
        wire = json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise SnapshotError("snapshot must be canonical finite JSON") from exc
    if len(wire) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("snapshot exceeds 1 MiB PRIVATE sidecar budget")
    return wire


def runtime_source_sha256():
    """Conservatively pin the exact CPU runtime code used for cache creation.

    A git/Termux update may change deterministic replay behavior without
    incrementing a fixture schema. Never skip recomputation under changed
    Python source files merely because an old HMAC snapshot still validates.
    """
    root = Path(__file__).resolve().parent
    paths = sorted(root.glob("*.py"), key=lambda item: item.name)
    if not paths:
        raise SnapshotError("runtime source modules not found")
    sha = hashlib.sha256()
    for file in paths:
        sha.update(file.name.encode("utf-8"))
        sha.update(bytes((0,)))
        sha.update(file.read_bytes())
        sha.update(bytes((0,)))
    return sha.hexdigest()


def _open_regular(path, *, secret):
    location = Path(path)
    if location.is_symlink():
        raise SnapshotError("PRIVATE snapshot/key path cannot be a symlink")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(location, flags)
    except OSError as exc:
        raise SnapshotError("missing/inaccessible PRIVATE snapshot or key") from exc
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise SnapshotError("PRIVATE snapshot/key must be a regular file")
        if os.name == "posix" and (stat.S_IMODE(mode) & 0o077):
            raise SnapshotError("PRIVATE snapshot/key must deny group/other access")
        size = os.fstat(fd).st_size
        max_size = 32 if secret else MAX_SNAPSHOT_BYTES
        if size > max_size:
            raise SnapshotError("PRIVATE snapshot/key exceeds size limit")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(max_size + 1)
        if len(data) > max_size:
            raise SnapshotError("PRIVATE snapshot/key exceeds size limit")
        return data
    finally:
        os.close(fd)


def read_key(key_file):
    raw = _open_regular(key_file, secret=True)
    if len(raw) != 32:
        raise SnapshotError("snapshot key must be exactly 32 random bytes")
    return raw


def _unique_pairs(pairs):
    out = {}
    for name, value in pairs:
        if name in out:
            raise SnapshotError("duplicate JSON object key in snapshot")
        out[name] = value
    return out


def load_snapshot(snapshot_file, key_file):
    """Return verified signed state or None if absent; never silently repair.

    Call BEFORE bootstrapping/writing the SQLite journal. If a sidecar exists
    and is invalid, the user must explicitly move it aside after diagnosis.
    """
    key = read_key(key_file)
    path = Path(snapshot_file)
    if not path.exists() and not path.is_symlink():
        return None
    raw = _open_regular(path, secret=False)
    try:
        envelope = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise SnapshotError("invalid UTF-8 JSON snapshot") from exc
    if (type(envelope) is not dict or set(envelope) !=
            {"schema", "state", "hmac_sha256"}
            or envelope["schema"] != SCHEMA or
            type(envelope["hmac_sha256"]) is not str or
            len(envelope["hmac_sha256"]) != 64 or
            any(char not in HEX for char in envelope["hmac_sha256"])):
        raise SnapshotError("unsupported or malformed signed snapshot envelope")
    state = envelope["state"]
    if (type(state) is not dict
            or state.get("schema") not in (STATE_SCHEMA, STATE_SCHEMA_V4)):
        raise SnapshotError("unsupported or malformed replay-state schema")
    expected = hmac.new(key, canonical(state), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope["hmac_sha256"]):
        raise SnapshotError("snapshot HMAC mismatch: refusing restore")
    # Exact canonical JSON blocks text-editor reformatting, duplicate keys,
    # aliases and non-canonical numeric/text representations.
    if canonical(envelope) != raw:
        raise SnapshotError("snapshot JSON is not canonical")
    return state


def save_snapshot(snapshot_file, key_file, state):
    """Atomic owner-only sidecar replacement; never touch SQLite source."""
    key = read_key(key_file)
    if (type(state) is not dict
            or state.get("schema") not in (STATE_SCHEMA, STATE_SCHEMA_V4)):
        raise SnapshotError("invalid snapshot state")
    mac = hmac.new(key, canonical(state), hashlib.sha256).hexdigest()
    raw = canonical({"schema": SCHEMA, "state": state, "hmac_sha256": mac})
    path = Path(snapshot_file)
    if not path.parent.is_dir() or path.is_symlink() or path.is_dir():
        raise SnapshotError("snapshot destination must be a normal local path")
    if path.exists():
        # Refuse to replace an existing unsafe file, even if OS permissions
        # otherwise allow os.replace(). Existing valid sidecars may be
        # replaced by a new 0600 file as the organism advances.
        _open_regular(path, secret=False)
    fd, temporary = tempfile.mkstemp(prefix=".mojum-snapshot-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as writer:
            if os.name == "posix":
                os.fchmod(writer.fileno(), 0o600)
            writer.write(raw)
            writer.flush()
            os.fsync(writer.fileno())
        # Check symlink substitution immediately before atomic publish.
        if path.is_symlink():
            raise SnapshotError("snapshot destination changed to a symlink")
        os.replace(temporary, path)
        if os.name == "posix" and hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"schema": SCHEMA, "state_sha256": hashlib.sha256(canonical(state)).hexdigest()}


def create_key_file(key_file):
    """Separate explicit key-provisioning operation, never implicit at boot."""
    path = Path(key_file)
    if not path.parent.is_dir() or path.is_symlink():
        raise SnapshotError("key directory must already exist and be local")
    fd = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as writer:
            fd = None
            writer.write(os.urandom(32))
            writer.flush()
            os.fsync(writer.fileno())
    except FileExistsError as exc:
        raise SnapshotError("refusing to replace existing snapshot key") from exc
    finally:
        if fd is not None:
            os.close(fd)
    return path

def main(argv=None):
    """Explicit one-off HMAC key provisioning; never generated on boot."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Create one 32-byte PRIVATE local snapshot HMAC key"
    )
    parser.add_argument("--create-key", type=Path, required=True,
                        help="fresh file in an existing owner-private directory")
    options = parser.parse_args(argv)
    create_key_file(options.create_key)
    # No key material, snapshot state or absolute private filename on stdout.
    print("PRIVATE_32_BYTE_KEY_CREATED_NO_REPLACEMENT")


if __name__ == "__main__":
    main()
