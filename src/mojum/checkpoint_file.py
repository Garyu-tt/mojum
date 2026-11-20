"""Offline, private checkpoint file transfer built atop validated bundle bytes.

Atomic *visibility* for new filenames on local filesystems with hard links;
not an authenticated backup, anti-rollback store, distributed checkpoint,
or guarantee of persistence across sudden power loss.
"""

import os
import stat
import tempfile

from .checkpoint_bundle import dumps, loads, unpack

MAX_WIRE_BYTES = 30_000_000
_TEMP_PREFIX = ".mojum-checkpoint-"


def export_checkpoint_file(path, bundle):
    """Write a canonical checkpoint to a previously unused filename.

    The file is first written to a private temporary sibling, flushed, then
    published with a same-filesystem hard link. Existing paths (including
    symlinks) are never replaced. On unsupported filesystems, fail rather
    than silently falling back to a non-atomic publication.
    """
    target = os.fspath(path)
    if type(target) is not str or not target:
        raise ValueError("checkpoint path must be text")
    raw = dumps(bundle).encode("ascii")
    if len(raw) > MAX_WIRE_BYTES:
        raise ValueError("oversized checkpoint file")

    directory = os.path.dirname(os.path.abspath(target))
    fd, temporary = tempfile.mkstemp(prefix=_TEMP_PREFIX, dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # os.link is a no-clobber atomic publication (when the filesystem
        # supports hard links). os.replace would silently overwrite an
        # existing organism checkpoint.
        os.link(temporary, target)
    finally:
        os.unlink(temporary)
    return target


def import_checkpoint_file(path, *, expected_organism_id, expected_revision):
    """Return (verified manifest, component bytes) after bounded local read.

    Both the organism ID and revision are mandatory caller-supplied bounds.
    They prevent mistaken cross-organism restores; they are *not* a trusted
    signature, authority proof or a rollback-resistant monotonic counter.
    """
    if type(expected_organism_id) is not str or not expected_organism_id:
        raise ValueError("expected organism identity required")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected checkpoint revision required")
    target = os.fspath(path)
    if type(target) is not str or not target:
        raise ValueError("checkpoint path must be text")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("checkpoint source must be a regular file")
        if info.st_size > MAX_WIRE_BYTES:
            raise ValueError("oversized checkpoint file")
        raw = stream.read(MAX_WIRE_BYTES + 1)
        if len(raw) > MAX_WIRE_BYTES:
            raise ValueError("oversized checkpoint file")
    try:
        wire = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("checkpoint file is not ASCII JSON") from exc
    bundle = loads(wire, expected_revision=expected_revision,
                   expected_organism_id=expected_organism_id)
    parts = unpack(bundle, expected_revision=expected_revision,
                   expected_organism_id=expected_organism_id)
    return bundle["manifest"], parts
