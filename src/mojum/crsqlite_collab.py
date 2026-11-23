"""Opt-in CR-SQLite v0.16.3 research bridge for PUBLIC_SYNTHETIC graph notes.

Only isolated :memory: SQLite connections; no Mojum journal, Noosphere, or network.
The *real* upstream extension is mandatory for native use, never silently faked.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

SCHEMA = 'mojum.crsqlite.public-notes/1'
TABLE = 'collab_notes'
MAX_NOTES = 64
MAX_TEXT = 512
MAX_ROWS = 128
MAX_PACKET = 65536
MAX_VERSION = (1 << 63) - 1
MAX_BLOB = 128
_ID = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_-]{0,47}$')
_HEX = re.compile(r'^[0-9a-f]{64}$')

SELECT_CHANGES = (
    'SELECT "table", "pk", "cid", "val", "col_version", "db_version", '
    'COALESCE("site_id", crsql_site_id()), "cl", "seq" '
    'FROM crsql_changes WHERE db_version > ? '
    'ORDER BY db_version, seq, site_id LIMIT ?'
)
INSERT_CHANGE = (
    'INSERT INTO crsql_changes '
    '("table", "pk", "cid", "val", "col_version", "db_version", "site_id", "cl", "seq") '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
)


class CollabError(ValueError):
    """Invalid or unauthorized research-only collaboration operation."""


def _bounded_int(value, *, minimum=0):
    return type(value) is int and minimum <= value <= MAX_VERSION


def _pairs_no_duplicates(items):
    out = {}
    for key, value in items:
        if key in out:
            raise CollabError('duplicate JSON key')
        out[key] = value
    return out


def _check_note_text(value):
    """Enforce the same strict, nonempty UTF-8 cell contract on local and wire writes."""
    if type(value) is not str or not value:
        raise CollabError('invalid public annotation text')
    try:
        size = len(value.encode('utf-8', 'strict'))
    except UnicodeEncodeError as exc:
        raise CollabError('invalid public annotation UTF-8') from exc
    if size > MAX_TEXT:
        raise CollabError('public annotation exceeds byte limit')
    return value


def _check_wire_row(row):
    if type(row) not in (tuple, list) or len(row) != 9:
        raise CollabError('CR-SQLite row must have exactly nine columns')
    table, pk, cid, value, col_version, db_version, site_id, cl, seq = row
    if table != TABLE or type(table) is not str:
        raise CollabError('non-collaborative table rejected')
    if type(pk) is not bytes or not (1 <= len(pk) <= MAX_BLOB):
        raise CollabError('invalid CR-SQLite packed primary key')
    if type(site_id) is not bytes or len(site_id) != 16:
        raise CollabError('invalid site id')
    if cid not in ('note', '-1') or type(cid) is not str:
        raise CollabError('unknown CR-SQLite cell column')
    if cid == 'note':
        _check_note_text(value)
    elif value is not None:
        raise CollabError('invalid tombstone value')
    if not (_bounded_int(col_version, minimum=1) and
            _bounded_int(db_version, minimum=1) and
            _bounded_int(cl) and _bounded_int(seq)):
        raise CollabError('invalid CR-SQLite clocks')
    return (table, pk, cid, value, col_version, db_version, site_id, cl, seq)


def _encoded_row(row):
    a = _check_wire_row(row)
    return [a[0], base64.b64encode(a[1]).decode('ascii'), a[2], a[3],
            a[4], a[5], base64.b64encode(a[6]).decode('ascii'), a[7], a[8]]


def _strict_b64(value, maximum):
    if type(value) is not str or len(value) > maximum * 4 // 3 + 8:
        raise CollabError('oversized binary field')
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise CollabError('invalid base64 field') from exc
    if base64.b64encode(decoded).decode('ascii') != value or len(decoded) > maximum:
        raise CollabError('noncanonical binary field')
    return decoded


def encode_packet(rows):
    """Encode a bounded nine-column CRR change set, without transport or secrets."""
    if type(rows) not in (list, tuple) or len(rows) > MAX_ROWS:
        raise CollabError('change count exceeds research budget')
    payload = {'schema': SCHEMA, 'classification': 'PUBLIC_SYNTHETIC',
               'table': TABLE, 'changes': [_encoded_row(row) for row in rows]}
    packet = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                        separators=(',', ':')).encode('ascii')
    if len(packet) > MAX_PACKET:
        raise CollabError('change packet exceeds budget')
    return packet


def decode_packet(packet):
    """Validate a complete, locally provided packet *before* SQLite touches it."""
    if type(packet) is not bytes or len(packet) > MAX_PACKET:
        raise CollabError('invalid packet byte budget')
    try:
        payload = json.loads(packet, object_pairs_hook=_pairs_no_duplicates,
                             parse_constant=lambda _: (_ for _ in ()).throw(CollabError('NaN denied')))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise CollabError('invalid packet JSON') from exc
    if type(payload) is not dict or set(payload) != {'schema', 'classification', 'table', 'changes'}:
        raise CollabError('invalid packet envelope')
    if (payload['schema'], payload['classification'], payload['table']) != (
            SCHEMA, 'PUBLIC_SYNTHETIC', TABLE):
        raise CollabError('unauthorized schema or classification')
    changes = payload['changes']
    if type(changes) is not list or len(changes) > MAX_ROWS:
        raise CollabError('invalid change count')
    result = []
    for row in changes:
        if type(row) is not list or len(row) != 9:
            raise CollabError('invalid change row')
        a = list(row)
        a[1] = _strict_b64(a[1], MAX_BLOB)
        a[6] = _strict_b64(a[6], 16)
        result.append(_check_wire_row(a))
    return tuple(result)


def _check_note_id(note_id):
    if type(note_id) is not str or not _ID.fullmatch(note_id):
        raise CollabError('invalid public annotation id')


class Scratchpad:
    """One disposable CRR with no network and no filesystem/database attachment."""

    def __init__(self, conn):
        self._conn = conn
        self._closed = False

    def _active(self):
        if self._closed:
            raise CollabError('scratchpad closed')
        return self._conn

    def put(self, note_id, note):
        _check_note_id(note_id)
        _check_note_text(note)
        conn = self._active()
        count = conn.execute('SELECT COUNT(*) FROM collab_notes').fetchone()[0]
        exists = conn.execute('SELECT 1 FROM collab_notes WHERE note_id = ?', (note_id,)).fetchone()
        if not exists and count >= MAX_NOTES:
            raise CollabError('annotation count budget exceeded')
        conn.execute('INSERT INTO collab_notes (note_id, note) VALUES (?, ?) '
                     'ON CONFLICT(note_id) DO UPDATE SET note = excluded.note', (note_id, note))

    def notes(self):
        return tuple(self._active().execute(
            'SELECT note_id, note FROM collab_notes ORDER BY note_id').fetchall())

    def export(self, since=0):
        if not _bounded_int(since):
            raise CollabError('invalid cursor')
        rows = self._active().execute(SELECT_CHANGES, (since, MAX_ROWS + 1)).fetchall()
        if len(rows) > MAX_ROWS:
            raise CollabError('change set too large; no partial export')
        return encode_packet(rows)

    def apply(self, packet, *, trusted_local_fixture=False):
        # The native CRR extension is not a hardened network parser; an authenticated,
        # replay-aware transport/peer admission layer is NOT included here.
        if trusted_local_fixture is not True:
            raise CollabError('remote / unauthenticated merge is forbidden')
        rows = decode_packet(packet)
        conn = self._active()
        conn.execute('BEGIN IMMEDIATE')
        try:
            for row in rows:
                conn.execute(INSERT_CHANGE, row)
            if conn.execute('SELECT COUNT(*) FROM collab_notes').fetchone()[0] > MAX_NOTES:
                raise CollabError('merge exceeds annotation count budget')
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        return len(rows)

    def close(self):
        if not self._closed:
            try:
                self._conn.execute('SELECT crsql_finalize()')
            finally:
                self._conn.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_scratchpad(*, enabled=False, classification=None,
                    extension_path=None, expected_sha256=None):
    """Load an explicitly trusted CR-SQLite .so/.dylib/.dll into :memory: only.

    A native SQLite extension is executable code. Verify the binary with an
    independently obtained SHA-256 and run in an OS-level sandbox for research.
    """
    if enabled is not True or classification != 'PUBLIC_SYNTHETIC':
        raise CollabError('explicit PUBLIC_SYNTHETIC opt-in required')
    if type(extension_path) is not str or not extension_path:
        raise CollabError('absolute local extension path required')
    if type(expected_sha256) is not str or not _HEX.fullmatch(expected_sha256):
        raise CollabError('pinned SHA-256 required')
    path = Path(extension_path)
    if not path.is_absolute() or path.suffix.lower() not in ('.so', '.dll', '.dylib'):
        raise CollabError('absolute native library path required')
    # Require a regular, non-symlink binary and refuse oversized libraries.
    # Symlink checks are not a full TOCTOU defence; use an isolated trusted host.
    try:
        st = path.lstat()
    except OSError as exc:
        raise CollabError('extension file unavailable') from exc
    if not stat.S_ISREG(st.st_mode) or not (0 < st.st_size <= 32 * 1024 * 1024):
        raise CollabError('untrusted extension file type or size')
    if path.is_symlink():
        raise CollabError('extension symlink forbidden')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise CollabError('extension checksum mismatch')

    conn = sqlite3.connect(':memory:', isolation_level=None)
    try:
        try:
            conn.enable_load_extension(True)
            try:
                conn.load_extension(str(path))
            finally:
                conn.enable_load_extension(False)
        except (AttributeError, sqlite3.Error, OSError) as exc:
            raise CollabError('trusted CR-SQLite extension unavailable') from exc
        # These functions fail shut if the loaded library is not the expected API.
        conn.execute('SELECT crsql_site_id()').fetchone()
        conn.execute('CREATE TABLE collab_notes '
                     '(note_id TEXT PRIMARY KEY NOT NULL, note TEXT NOT NULL)')
        conn.execute("SELECT crsql_as_crr('collab_notes')").fetchone()
        return Scratchpad(conn)
    except BaseException:
        conn.close()
        raise
