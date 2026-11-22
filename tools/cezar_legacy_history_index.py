#!/usr/bin/env python3
"""Private, read-only indexing of historical Cezar task metadata.

Never extracts tar members or imports/restarts historical work. Identical task
text is grouped only *within the same project* and the original records are
preserved for audit. No prompts, API credentials or raw model outputs in index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import uuid

PROJECTS = (
    "neurocms-r43-review-candidate",
    "mechanicus-director",
    "neurocms-dev-r43",
)
SCHEMA = "mojum.cezar.legacy-history-index.v1"
MAX_COMPRESSED_BYTES = 128 * 1024 * 1024
MAX_RUNS_JSON_BYTES = 4 * 1024 * 1024
MAX_RUNS_PER_PROJECT = 2000
STATUS = frozenset({"done", "failed", "cancelled", "review", "waiting",
                    "running", "queued", "blocked"})
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class IndexRejected(ValueError):
    pass


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IndexRejected("duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(_):
    raise IndexRejected("nonfinite JSON")


def _hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_private_file(path: Path):
    if not path.is_absolute() or os.path.realpath(path) != os.path.abspath(path):
        raise IndexRejected("noncanonical source path")
    info = os.lstat(path)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
        raise IndexRejected("archive must be owner-only regular file")
    if not 1 <= info.st_size <= MAX_COMPRESSED_BYTES:
        raise IndexRejected("archive size out of bounds")


def _check_private_dir(path: Path):
    if not path.is_absolute() or os.path.realpath(path) != os.path.abspath(path):
        raise IndexRejected("noncanonical output directory")
    info = os.lstat(path)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise IndexRejected("index directory must be owner-only")


def _runs(raw: bytes, project: str):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs,
                          parse_constant=_reject_constant)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise IndexRejected("malformed legacy task JSON") from exc
    if isinstance(data, dict):
        data = data.get("runs")
    if not isinstance(data, list) or not len(data) <= MAX_RUNS_PER_PROJECT:
        raise IndexRejected("unexpected legacy runs collection")
    for run in data:
        if not isinstance(run, dict):
            raise IndexRejected("invalid legacy run record")
        raw_id = run.get("id")
        if not isinstance(raw_id, str):
            raise IndexRejected("invalid run ID")
        try:
            key_id = str(uuid.UUID(raw_id))
        except (ValueError, AttributeError) as exc:
            raise IndexRejected("malformed run UUID") from exc
        if key_id != raw_id.lower():
            raise IndexRejected("noncanonical run UUID")
        status = run.get("status")
        if not isinstance(status, str) or status not in STATUS:
            raise IndexRejected("unexpected legacy run status")
        task = run.get("task")
        if not isinstance(task, str) or len(task) > 100000 or not task.strip():
            raise IndexRejected("missing legacy task text")
        created = run.get("createdAt")
        if not isinstance(created, str) or len(created) > 64:
            raise IndexRejected("malformed createdAt")
        title = run.get("title")
        if title is None:
            title = ""
        if not isinstance(title, str) or len(title) > 2000:
            raise IndexRejected("malformed title")
        # The grouping is deliberately project-scoped. A task in NeuroCMS
        # never becomes a completed Mojum task just because words match.
        task_digest = _hex(task.encode("utf-8"))
        group = _hex((project + "\0" + task_digest).encode("utf-8"))
        yield {
            "id": key_id, "status": status, "created_at": created[:64],
            "task_sha256": task_digest, "group_key": group,
            "title_sha256": _hex(title.encode("utf-8")),
        }


def build_index(archive: Path) -> dict:
    _check_private_file(archive)
    expected = {
        f"projects/{project}/.ai/cezar/runs.json": project
        for project in PROJECTS
    }
    source_hash = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            source_hash.update(chunk)
    per_project = {}
    with tarfile.open(archive, "r:gz") as tf:
        members = {}
        for member in tf:
            if member.name not in expected:
                continue
            if member.name in members:
                raise IndexRejected("duplicate archive metadata member")
            if not member.isfile() or member.size > MAX_RUNS_JSON_BYTES:
                raise IndexRejected("nonregular or oversized runs.json")
            members[member.name] = member
        if set(members) != set(expected):
            raise IndexRejected("archive is missing expected projects")
        for path, project in expected.items():
            stream = tf.extractfile(members[path])
            if stream is None:
                raise IndexRejected("metadata member unavailable")
            with stream:
                content = stream.read(MAX_RUNS_JSON_BYTES + 1)
            if len(content) != members[path].size:
                raise IndexRejected("inconsistent member size")
            per_project[project] = list(_runs(content, project))
    groups = {}
    seen_ids = set()
    for project in PROJECTS:
        for record in per_project[project]:
            if record["id"] in seen_ids:
                raise IndexRejected("run UUID repeated in multiple projects")
            seen_ids.add(record["id"])
            k = record["group_key"]
            if k not in groups:
                groups[k] = {
                    "key": k, "project": project,
                    "task_sha256": record["task_sha256"],
                    "run_ids": [], "statuses": [],
                }
            groups[k]["run_ids"].append(record["id"])
            groups[k]["statuses"].append(record["status"])
    sorted_groups = sorted(groups.values(), key=lambda g: (g["project"], g["key"]))
    for group in sorted_groups:
        group["run_count"] = len(group["run_ids"])
        group["repeats"] = group["run_count"] - 1
    status_counts = {
        name: sum(r["status"] == name for v in per_project.values() for r in v)
        for name in sorted(STATUS)
    }
    total = sum(len(value) for value in per_project.values())
    return {
        "schema": SCHEMA,
        "source_archive_sha256": source_hash.hexdigest(),
        "read_only_legacy": True,
        "live_cezar_tasks_created": 0,
        "prompts_or_secrets_exported": False,
        "projects": [{"name": p, "total_runs": len(per_project[p]),
                      "unique_tasks": sum(g["project"] == p for g in sorted_groups)}
                     for p in PROJECTS],
        "total_runs": total,
        "unique_project_tasks": len(groups),
        "duplicate_attempts": total - len(groups),
        "status_counts": {s: n for s, n in status_counts.items() if n},
        "groups": sorted_groups,
    }


def save_index(index: dict, output: Path):
    parent = output.parent
    _check_private_dir(parent)
    if not output.name or output.name in (".", "..") or "/" in output.name:
        raise IndexRejected("invalid output name")
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        target = output.name
        try:
            existing = os.stat(target, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.geteuid()
                    or stat.S_IMODE(existing.st_mode) & 0o077):
                raise IndexRejected("unsafe existing index")
        tmp = f".{target}.tmp.{os.getpid()}"
        data = (json.dumps(index, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=fd)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(tmp, dir_fd=fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        index = build_index(args.archive)
        save_index(index, args.output)
    except (IndexRejected, OSError, tarfile.TarError) as exc:
        parser.exit(2, "legacy index rejected: " + type(exc).__name__ + "\n")
    # Summary only. Never dump imported tasks/prompts or archive bytes.
    print(json.dumps({key: index[key] for key in
                      ("total_runs", "unique_project_tasks", "duplicate_attempts",
                       "status_counts", "live_cezar_tasks_created")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
