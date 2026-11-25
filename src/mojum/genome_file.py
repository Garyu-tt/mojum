"""Bounded GENESIS seed-file admission before CLI journal access.

The canonical seed validator remains authoritative for the decoded genotype.
This boundary additionally rejects blocking file types and ambiguous JSON.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from .genesis import validate_seed


MAX_GENOME_BYTES = 256 * 1024
_UNREADABLE = "--genome requires a readable UTF-8 JSON file"


class GenomeFileError(ValueError):
    """Rejected seed file or JSON encoding, before genotype validation."""


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            # Do not print arbitrary input keys on the caller's console.
            raise GenomeFileError("--genome contains a duplicate JSON object key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise GenomeFileError("--genome must use finite JSON numbers")


def load_seed_file(path: str | bytes | Path) -> dict:
    """Read one regular UTF-8 JSON file within a 256 KiB byte budget.

    Symlinks to regular files are supported; the opened descriptor determines
    the file type. Numeric descriptors are never accepted or consumed. POSIX
    opens are nonblocking so a writerless FIFO cannot hang before inspection.
    File/JSON failures raise GenomeFileError; invalid genotypes retain the
    canonical GenesisError contract.
    """
    try:
        filename = os.fspath(path)
    except TypeError as exc:
        raise GenomeFileError("--genome requires a filesystem path") from exc
    if os.name == "posix" and not getattr(os, "O_NONBLOCK", 0):
        raise GenomeFileError("--genome cannot be read safely without O_NONBLOCK")

    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(filename, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise GenomeFileError("--genome must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1  # The stream owns the descriptor from here.
            raw = stream.read(MAX_GENOME_BYTES + 1)
    except GenomeFileError:
        raise
    except (OSError, ValueError) as exc:
        raise GenomeFileError(_UNREADABLE) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(raw) > MAX_GENOME_BYTES:
        raise GenomeFileError("--genome exceeds the 256 KiB byte limit")
    try:
        seed = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except GenomeFileError:
        raise
    except (ValueError, RecursionError) as exc:
        raise GenomeFileError(_UNREADABLE) from exc
    return validate_seed(seed)
