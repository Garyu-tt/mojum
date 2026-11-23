"""Hermetic CR-SQLite wire tests; native extension must be independently verified."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from mojum.crsqlite_collab import (
    CollabError, Scratchpad, SCHEMA, TABLE, decode_packet, encode_packet,
    open_scratchpad, MAX_PACKET, MAX_ROWS, INSERT_CHANGE, SELECT_CHANGES,
)

GOOD = (TABLE, b'\x01\x09\x01', 'note', 'hello', 1, 3,
        bytes(range(16)), 1, 0)


class PacketTests(unittest.TestCase):
    def test_roundtrip_and_repeatable_bytes(self):
        packet = encode_packet([GOOD])
        self.assertEqual(packet, encode_packet([GOOD]))
        self.assertEqual(decode_packet(packet), (GOOD,))
        self.assertLess(len(packet), 512)

    def test_empty_delta(self):
        self.assertEqual(decode_packet(encode_packet([])), ())

    def test_unknown_table_and_column(self):
        for field, changed in ((0, 'organism_events'), (2, 'brain_state')):
            row = list(GOOD)
            row[field] = changed
            with self.subTest(field=field), self.assertRaises(CollabError):
                encode_packet([row])

    def test_no_private_or_noncanonical_envelope(self):
        data = json.loads(encode_packet([GOOD]))
        for field, changed in [('classification', 'PRIVATE'),
                               ('schema', 'mojum.memory/1'), ('table', 'journals')]:
            bad = dict(data)
            bad[field] = changed
            with self.subTest(field=field), self.assertRaises(CollabError):
                decode_packet(json.dumps(bad).encode())
        with self.assertRaises(CollabError):
            decode_packet(b'{"schema":1,"schema":2}')
        with self.assertRaises(CollabError):
            decode_packet(b'{' + b' ' * MAX_PACKET + b'}')

    def test_bad_binary_and_bool_clocks(self):
        data = json.loads(encode_packet([GOOD]))
        data['changes'][0][1] = 'AQ==='
        with self.assertRaises(CollabError):
            decode_packet(json.dumps(data).encode())
        data = json.loads(encode_packet([GOOD]))
        data['changes'][0][4] = True
        with self.assertRaises(CollabError):
            decode_packet(json.dumps(data).encode())

    def test_row_and_packet_budgets(self):
        with self.assertRaises(CollabError):
            encode_packet([GOOD] * (MAX_ROWS + 1))
        with self.assertRaises(CollabError):
            encode_packet([GOOD[:3] + ('x' * 513,) + GOOD[4:]])

    def test_tombstone(self):
        tomb = list(GOOD)
        tomb[2:4] = ['-1', None]
        self.assertEqual(decode_packet(encode_packet([tomb])), (tuple(tomb),))
        tomb[3] = 'something'
        with self.assertRaises(CollabError):
            encode_packet([tomb])

    def test_no_float_nan_and_no_oversized_pk(self):
        for row in [GOOD[:4] + (float('nan'),) + GOOD[5:],
                    GOOD[:1] + (b'A' * 129,) + GOOD[2:],
                    GOOD[:6] + (b'bad',) + GOOD[7:]]:
            with self.assertRaises(CollabError):
                encode_packet([row])


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows


class FakeCRR:
    """Only a query recorder, not a counterfeit merge engine."""
    def __init__(self):
        self.calls = []
        self.closed = False
        self.count = 0
        self.exists = False
        self.fail_insert = False
    def execute(self, query, params=()):
        self.calls.append((query, tuple(params)))
        if query == SELECT_CHANGES:
            return FakeCursor([GOOD])
        if query == INSERT_CHANGE and self.fail_insert:
            raise RuntimeError('simulated native merge failure')
        if query.startswith('SELECT COUNT'):
            return FakeCursor([(self.count,)])
        if query.startswith('SELECT 1 FROM'):
            return FakeCursor([(1,)] if self.exists else [])
        if query.startswith('SELECT note_id'):
            return FakeCursor([('a', 'public')])
        return FakeCursor([])
    def close(self):
        self.closed = True


class ScratchpadBoundaryTests(unittest.TestCase):
    def test_no_implicit_extension(self):
        for kwargs in ({}, {'enabled': True, 'classification': 'PRIVATE'},
                       {'enabled': True, 'classification': 'PUBLIC_SYNTHETIC'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CollabError):
                open_scratchpad(**kwargs)

    def test_native_path_and_checksum_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp, 'crsqlite.so')
            lib.write_bytes(b'not a library')
            with self.assertRaisesRegex(CollabError, 'checksum'):
                open_scratchpad(enabled=True, classification='PUBLIC_SYNTHETIC',
                                extension_path=str(lib), expected_sha256='0' * 64)
            with self.assertRaises(CollabError):
                open_scratchpad(enabled=True, classification='PUBLIC_SYNTHETIC',
                                extension_path='relative.so', expected_sha256='0' * 64)
            alias = Path(tmp, 'alias.so')
            alias.symlink_to(lib)
            with self.assertRaises(CollabError):
                open_scratchpad(enabled=True, classification='PUBLIC_SYNTHETIC',
                                extension_path=str(alias), expected_sha256='0' * 64)

    def test_export_query_is_bounded_parameterized(self):
        conn = FakeCRR()
        s = Scratchpad(conn)
        self.assertEqual(decode_packet(s.export()), (GOOD,))
        self.assertEqual(conn.calls[-1], (SELECT_CHANGES, (0, MAX_ROWS + 1)))
        for bad in [True, -1, 1.1, 2**64]:
            with self.assertRaises(CollabError):
                s.export(bad)

    def test_apply_requires_explicit_trusted_fixture(self):
        conn = FakeCRR()
        s = Scratchpad(conn)
        packet = encode_packet([GOOD])
        with self.assertRaises(CollabError):
            s.apply(packet)
        self.assertEqual(conn.calls, [])
        self.assertEqual(s.apply(packet, trusted_local_fixture=True), 1)
        self.assertIn((INSERT_CHANGE, GOOD), conn.calls)
        self.assertIn(('COMMIT', ()), conn.calls)

    def test_rollback_on_native_error(self):
        conn = FakeCRR()
        conn.fail_insert = True
        s = Scratchpad(conn)
        with self.assertRaises(RuntimeError):
            s.apply(encode_packet([GOOD]), trusted_local_fixture=True)
        self.assertEqual(conn.calls[-1], ('ROLLBACK', ()))

    def test_put_disallows_hostile_text_and_exhaustion(self):
        conn = FakeCRR()
        s = Scratchpad(conn)
        for note_id, note in [('bad/id', 'hello'), ('ok', ''), ('ok', 'X' * 513),
                              ('ok', True)]:
            with self.assertRaises(CollabError):
                s.put(note_id, note)
        conn.count = 64
        with self.assertRaises(CollabError):
            s.put('new', 'hello')
        conn.exists = True
        s.put('new', 'updated')
        self.assertTrue(any('ON CONFLICT' in q for q, _ in conn.calls))

    def test_close_and_guard_against_reuse(self):
        conn = FakeCRR()
        s = Scratchpad(conn)
        self.assertEqual(s.notes(), (('a', 'public'),))
        s.close()
        s.close()
        self.assertTrue(conn.closed)
        self.assertEqual(sum('crsql_finalize' in q for q, _ in conn.calls), 1)
        with self.assertRaises(CollabError):
            s.notes()


if __name__ == '__main__':
    unittest.main()
