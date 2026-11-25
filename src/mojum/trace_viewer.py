"""Materialize the bundled, standalone offline Aquarium trace viewer."""
from __future__ import annotations

import argparse
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile


SCHEMA = "mojum.trace-viewer.export/1"


class TraceViewerError(ValueError):
    """The requested viewer destination is already occupied."""


def export_viewer(destination):
    """Write the complete bundled HTML to a new path without replacing files.

    The resource is an exact copy of examples/aquarium_trace_viewer.html.
    No journal or trace is opened, and no browser or server is started.
    """
    path = Path(destination)
    if path.exists() or path.is_symlink():
        raise TraceViewerError("viewer output already exists; choose a new path")
    data = files("mojum").joinpath("data", "aquarium_trace_viewer.html").read_bytes()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".mojum-viewer-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Publish only complete bytes. The exclusive link also refuses an
        # output created by a competing process after the initial check.
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise TraceViewerError("viewer output already exists; choose a new path") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return {"schema": SCHEMA, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the standalone offline Aquarium trace viewer")
    parser.add_argument("--output", type=Path, required=True,
                        help="new HTML file to create; existing paths are never overwritten")
    args = parser.parse_args(argv)
    try:
        receipt = export_viewer(args.output)
    except TraceViewerError as exc:
        parser.error(str(exc))
    except OSError:
        parser.error("cannot write viewer; check the output directory and permissions")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
