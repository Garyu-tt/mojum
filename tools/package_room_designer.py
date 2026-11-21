"""Generate a self-contained offline HTML room editor from pinned local sources.

The original paired HTML/JS remain canonical developer sources. This creates
a NEW owner-only portable file on the operator's filesystem. No npm, server,
network, SQLite access or local browser execution is performed by the packer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from html.parser import HTMLParser
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "tools" / "room_designer.html"
JS = ROOT / "tools" / "room_designer_core.js"
HISTORY_JS = ROOT / "tools" / "room_designer_history.js"
WALK_JS = ROOT / "tools" / "room_walk_preview.js"
SCRIPT_TAG = '<script src="./room_designer_core.js"></script>'
HISTORY_TAG = '<script src="./room_designer_history.js"></script>'
WALK_TAG = '<script src="./room_walk_preview.js"></script>'
MAX_SOURCE_BYTES = 1_048_576


class PackagingError(ValueError):
    """Fail closed on stale source, dangerous output or broken script contract."""


class _ScriptSrcScanner(HTMLParser):
    """Parse quoted HTML attributes, including a literal '>' before src."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and any(name == "src" for name, _ in attrs):
            self.tags.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def _external_script_tags(html):
    scanner = _ScriptSrcScanner()
    scanner.feed(html)
    scanner.close()
    return scanner.tags


def _read_small_source(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise PackagingError("room designer source must be a regular nonsymlink file")
    with path.open("rb") as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise PackagingError("room designer source size outside 1 MiB budget")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackagingError("room designer source is not UTF-8") from exc


def standalone_html(*, html_path=HTML, js_path=JS,
                    history_path=HISTORY_JS, walk_path=WALK_JS) -> bytes:
    """Bundle every pinned local classic script; no sibling JS required."""
    html = _read_small_source(Path(html_path))
    scripts = (
        (SCRIPT_TAG, js_path, "MojumRoomDesignerCore"),
        (HISTORY_TAG, history_path, "MojumRoomHistory"),
        (WALK_TAG, walk_path, "MojumWalkPreview"),
    )
    # Fail closed if the template changes dependencies without updating
    # the bundler; external scripts would break file:// offline portability.
    if any(html.count(tag) != 1 for tag, _, _ in scripts):
        raise PackagingError("expected exactly one pinned local script tag")
    external = _external_script_tags(html)
    if sorted(external) != sorted(tag.split("</script>", 1)[0] for tag, _, _ in scripts):
        raise PackagingError("unexpected external editor script dependency")
    for tag, source, global_name in scripts:
        js = _read_small_source(Path(source))
        if "</script" in js.lower() or "<!--" in js:
            raise PackagingError("embedded JavaScript contains an unsafe HTML delimiter")
        if global_name not in js:
            raise PackagingError("wrong room editor JavaScript source")
        html = html.replace(
            tag, "<script>\n" + js.rstrip() + "\n</script>", 1
        )
    if _external_script_tags(html):
        raise PackagingError("portable editor still requires a sibling script")
    if len(html.encode("utf-8")) > 2 * MAX_SOURCE_BYTES:
        raise PackagingError("single file exceeds bounded portable HTML budget")
    return html.encode("utf-8")

def write_standalone(destination, *, html_path=HTML, js_path=JS):
    """Publish via durable temp + exclusive hard link; never clobber a file."""
    path = Path(destination)
    if path.suffix.lower() != ".html" or path.name in (".", ".."):
        raise PackagingError("destination must be a new .html file")
    if path.is_symlink() or path.exists():
        raise PackagingError("refusing to overwrite existing room designer HTML")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise PackagingError("destination parent must be an existing nonsymlink folder")
    data = standalone_html(html_path=html_path, js_path=js_path)
    fd = None
    temp_path = None
    try:
        fd, name = tempfile.mkstemp(prefix=".mojum-room-", suffix=".part", dir=parent)
        temp_path = Path(name)
        with os.fdopen(fd, "wb") as output:
            fd = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Exclusive link is atomic on one local filesystem and refuses
        # collisions even if another creator appeared after preflight.
        os.link(temp_path, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except FileExistsError as exc:
        raise PackagingError("output appeared concurrently; no overwrite") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return {
        "status": "NEW_STANDALONE_OFFLINE_ROOM_DESIGNER_WRITTEN",
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "file": str(path),
        "privacy": "local_file_only_no_journal_created",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pack Mojum room designer as NEW single-file offline HTML")
    parser.add_argument("--out", type=Path, required=True,
                        help="new .html filename; existing files never overwritten")
    args = parser.parse_args(argv)
    try:
        result = write_standalone(args.out)
    except (PackagingError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
