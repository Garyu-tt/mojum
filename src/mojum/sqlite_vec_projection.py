"""Optional, local sqlite-vec derived index for independently anchored Noosphere history.

No canonical journal writes, embeddings/model inference, automatic extension loading,
networking or plaintext payload persistence. Every index is scoped to one owner,
visibility, validity instant, model, dimension and *exact* source chain head.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import sqlite3
import stat
import struct
import tempfile

from mojum.noosphere import LocalNoosphere

INDEX_SCHEMA = "mojum.noosphere.sqlite-vec-index/1"
SQLITE_VEC_VERSION = "0.1.9"
_MAX_RECORDS = 4096
_MAX_DIM = 256
_VISIBILITIES = frozenset(("PUBLIC", "PRIVATE", "RESTRICTED"))


class VectorIndexError(ValueError):
    """An index, scope, dependency, or derived-data contract is invalid."""


def _text(value, name, limit=256):
    if type(value) is not str or not value or len(value) > limit:
        raise VectorIndexError(f"invalid {name}")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise VectorIndexError(f"invalid {name} UTF-8") from exc
    return value


def _head(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise VectorIndexError("expected independently trusted 64-character source head")
    return value


def _scope(owner, visibility, model, dimension, valid_at_ns, allow_nonpublic):
    _text(owner, "owner")
    _text(model, "model", 128)
    if type(visibility) is not str or visibility not in _VISIBILITIES:
        raise VectorIndexError("invalid visibility")
    if type(allow_nonpublic) is not bool or (visibility != "PUBLIC" and not allow_nonpublic):
        raise VectorIndexError("nonpublic scope needs explicit local opt-in")
    if type(dimension) is not int or not 1 <= dimension <= _MAX_DIM:
        raise VectorIndexError("invalid vector dimension")
    if valid_at_ns is not None and (type(valid_at_ns) is not int or not 0 <= valid_at_ns < 2**63):
        raise VectorIndexError("invalid validity timestamp")


def _vector(value, dimension):
    if type(value) not in (tuple, list) or len(value) != dimension:
        raise VectorIndexError("vector dimension mismatch")
    values = []
    for number in value:
        if type(number) not in (int, float) or abs(number) > 1_000_000 or not math.isfinite(number):
            raise VectorIndexError("vector contains non-finite or out-of-budget number")
        values.append(float(number))
    return struct.pack("<" + "f" * dimension, *values)


def prepare_index(source_path, *, owner, trusted_head, visibility, model,
                  dimension, vectors, valid_at_ns=None, allow_nonpublic=False):
    """Validate exact anchored history and return bounded derived rows (no payload).

    ``vectors`` is an explicit event_id -> list[float] supplied by a trusted
    caller. This module does not generate embeddings, touch external services,
    or assume that the hash chain authenticates itself. Missing IDs are omitted;
    unknown IDs are rejected to prevent mixing other organisms' data.
    """
    _scope(owner, visibility, model, dimension, valid_at_ns, allow_nonpublic)
    _head(trusted_head)
    if type(vectors) is not dict or len(vectors) > _MAX_RECORDS:
        raise VectorIndexError("invalid or oversized embedding map")
    packed = {}
    for event_id, vector in vectors.items():
        _text(event_id, "event ID")
        packed[event_id] = _vector(vector, dimension)
    rows = []
    matched = set()
    with LocalNoosphere(source_path, owner, read_only=True) as source:
        # Verification and enumeration must share one SQLite read snapshot.
        source._db.execute("BEGIN")
        try:
            # Preflight at most _MAX_RECORDS + 1 rows in this exact SQLite
            # snapshot. Do not hash/parse the entire hostile journal first.
            if source._db.execute(
                "SELECT 1 FROM events ORDER BY seq LIMIT 1 OFFSET ?",
                (_MAX_RECORDS,),
            ).fetchone() is not None:
                raise VectorIndexError("source scan exceeds CPU budget")
            proof = source.verify_chain(external_expected_head=trusted_head, strict_wire=True)
            if proof["count"] > _MAX_RECORDS:
                raise VectorIndexError("source scan exceeds CPU budget")
            for record in source.iter_events(include_nonpublic=(visibility != "PUBLIC"),
                                             valid_at_ns=valid_at_ns):
                event = record["event"]
                if event["visibility"] != visibility:
                    continue
                event_id = event["event_id"]
                if event_id not in packed:
                    continue
                matched.add(event_id)
                rows.append((event_id, record["event_hash"], packed[event_id]))
            if matched != set(packed):
                raise VectorIndexError("embedding references absent, expired or unauthorized event")
            source._db.execute("ROLLBACK")
        except BaseException:
            source._db.execute("ROLLBACK")
            raise
    rows.sort(key=lambda r: r[0])
    return tuple(rows)


def _load_vec(db):
    try:
        if importlib.metadata.version("sqlite-vec") != SQLITE_VEC_VERSION:
            raise VectorIndexError("sqlite-vec version does not match pinned version")
        import sqlite_vec  # noqa: PLC0415 - explicit opt-in only
    except importlib.metadata.PackageNotFoundError as exc:
        raise VectorIndexError("optional sqlite-vec==0.1.9 is not installed") from exc
    db.enable_load_extension(True)
    try:
        sqlite_vec.load(db)
    finally:
        db.enable_load_extension(False)
    version = db.execute("SELECT vec_version()").fetchone()[0]
    if str(version).lstrip("v") != SQLITE_VEC_VERSION:
        raise VectorIndexError("loaded sqlite-vec runtime version mismatch")


def _path_guard(index_path, source_path=None, *, nonpublic=False):
    path = Path(index_path)
    if str(path) == ":memory:" or path.suffix != ".sqlite" or not path.name:
        raise VectorIndexError("index must be a separate .sqlite disk file")
    if path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
        raise VectorIndexError("unsafe index path")
    if path.exists() and not path.is_file():
        raise VectorIndexError("index destination is not a regular file")
    if source_path is not None and path.resolve() == Path(source_path).resolve():
        raise VectorIndexError("index cannot replace the authoritative journal")
    if nonpublic and os.name == "posix":
        if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise VectorIndexError("nonpublic index requires an owner-only directory (0700)")
        if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise VectorIndexError("nonpublic index requires an owner-only file (0600)")
    return path


def _metadata(owner, visibility, model, dimension, trusted_head, valid_at_ns):
    return {"schema": INDEX_SCHEMA, "owner": owner, "visibility": visibility,
            "model": model, "dimension": str(dimension), "source_head": trusted_head,
            "valid_at_ns": "none" if valid_at_ns is None else str(valid_at_ns),
            "sqlite_vec": SQLITE_VEC_VERSION}


def _rows_digest(rows):
    """Checksum sorted event IDs, source hashes and their stored float32 bytes."""
    sha = hashlib.sha256(b"mojum.sqlite-vec.rows/1\\0")
    for event_id, event_hash, packed in rows:
        for raw in (event_id.encode("utf-8"), event_hash.encode("ascii"), bytes(packed)):
            sha.update(len(raw).to_bytes(4, "big"))
            sha.update(raw)
    return sha.hexdigest()


def _existing_meta(path):
    try:
        with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            return dict(db.execute("SELECT k,v FROM metadata"))
    except sqlite3.Error as exc:
        raise VectorIndexError("existing derived index is corrupt; rebuild into a NEW path") from exc


def rebuild_index(source_path, index_path, *, owner, trusted_head, visibility,
                  model, dimension, vectors, valid_at_ns=None,
                  allow_nonpublic=False, replace=False):
    """Write an isolated derived SQLite file; atomic replace on explicit request.

    Rebuilding is the only deletion/revocation path. A stale source head always
    requires a fresh anchor and complete rebuild, never incremental mutation.
    """
    if type(replace) is not bool:
        raise VectorIndexError("replace must be boolean")
    rows = prepare_index(source_path, owner=owner, trusted_head=trusted_head,
                         visibility=visibility, model=model, dimension=dimension,
                         vectors=vectors, valid_at_ns=valid_at_ns,
                         allow_nonpublic=allow_nonpublic)
    path = _path_guard(index_path, source_path, nonpublic=(visibility != "PUBLIC"))
    meta = _metadata(owner, visibility, model, dimension, trusted_head, valid_at_ns)
    meta["rows_sha256"] = _rows_digest(rows)
    if path.exists():
        if not replace:
            raise VectorIndexError("index already exists; explicit replace required")
        old = _existing_meta(path)
        if any(old.get(k) != meta[k] for k in ("schema", "owner", "visibility", "model", "dimension")):
            raise VectorIndexError("cannot replace a different index scope")
    fd, tmp_name = tempfile.mkstemp(prefix=".mojum-vec-", suffix=".sqlite", dir=path.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(tmp_name)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA trusted_schema=OFF")
            _load_vec(db)
            with db:
                db.execute("CREATE TABLE metadata (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
                db.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(meta.items()))
                db.execute("CREATE TABLE records (rowid INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, event_hash TEXT NOT NULL)")
                db.execute(f"CREATE VIRTUAL TABLE vectors USING vec0(embedding float[{dimension}])")
                for i, (event_id, event_hash, vector) in enumerate(rows, 1):
                    db.execute("INSERT INTO records VALUES (?, ?, ?)",
                               (i, event_id, event_hash))
                    db.execute("INSERT INTO vectors(rowid, embedding) VALUES (?, ?)", (i, vector))
        os.replace(tmp_name, path)
        if os.name == "posix":
            path.chmod(0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return {"schema": INDEX_SCHEMA, "owner": owner, "visibility": visibility,
            "source_head": trusted_head, "count": len(rows), "model": model,
            "dimension": dimension, "valid_at_ns": valid_at_ns}


def search_index(index_path, *, owner, trusted_head, visibility, model,
                 dimension, query, k=5, valid_at_ns=None, allow_nonpublic=False):
    """Read only one matching index scope, returning IDs/hashes, never payloads."""
    _scope(owner, visibility, model, dimension, valid_at_ns, allow_nonpublic)
    _head(trusted_head)
    if type(k) is not int or not 1 <= k <= 100:
        raise VectorIndexError("invalid result budget")
    packed = _vector(query, dimension)
    path = _path_guard(index_path, nonpublic=(visibility != "PUBLIC"))
    if not path.is_file():
        raise VectorIndexError("derived index missing; rebuild required")
    meta = _metadata(owner, visibility, model, dimension, trusted_head, valid_at_ns)
    with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        stored_meta = dict(db.execute("SELECT k,v FROM metadata"))
        if any(stored_meta.get(key) != value for key, value in meta.items()):
            raise VectorIndexError("index source head/scope mismatch; rebuild required")
        _load_vec(db)
        # Fetch the whole bounded population, then break exact-score ties by ID.
        # Prevents vec0's internal tie order from deciding reproducibility.
        count = db.execute("SELECT count(*) FROM records").fetchone()[0]
        if count > _MAX_RECORDS:
            raise VectorIndexError("derived index exceeds budget")
        stored_rows = db.execute("""
            SELECT records.event_id, records.event_hash, vectors.embedding
              FROM records JOIN vectors ON records.rowid = vectors.rowid
             ORDER BY records.event_id
        """).fetchall()
        if len(stored_rows) != count or _rows_digest(stored_rows) != stored_meta.get("rows_sha256"):
            raise VectorIndexError("derived index contents do not match rebuild checksum")
        if count == 0:
            return []
        matches = db.execute("""
            SELECT records.event_id, records.event_hash, nearest.distance
              FROM (SELECT rowid, distance FROM vectors
                     WHERE embedding MATCH ? AND k = ?) AS nearest
              JOIN records ON records.rowid = nearest.rowid
        """, (packed, count)).fetchall()
        if len(matches) != count:
            raise VectorIndexError("incomplete or corrupt derived vector index")
        return [dict(event_id=rid, event_hash=h, distance=float(distance))
                for rid, h, distance in sorted(matches, key=lambda row: (row[2], row[0]))[:k]]
