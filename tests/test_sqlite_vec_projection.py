"""Offline contract tests; actual sqlite-vec test activates only if pinned extra exists."""
from contextlib import closing
import importlib.metadata
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mojum.noosphere import LocalNoosphere, EVENT_SCHEMA, NoosphereError
from mojum.sqlite_vec_projection import (
    SQLITE_VEC_VERSION, VectorIndexError, prepare_index, rebuild_index, search_index,
)

OWNER = "synthetic-index-agent"


def event(identifier, visibility="PUBLIC", *, timestamp=5, valid_to=None):
    return {"schema": EVENT_SCHEMA, "event_id": identifier, "agent_id": OWNER,
            "topic": "fixture", "observed_at_ns": timestamp,
            "recorded_at_ns": timestamp + 1, "valid_from_ns": 0,
            "valid_to_ns": valid_to, "visibility": visibility,
            "source": "synthetic:test", "source_revision": "r1",
            "payload": {"private_secret": "NOT_FOR_DERIVED_SQLITE"}}


class SqliteVecProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.journal = self.dir / "canonical.sqlite"
        with LocalNoosphere(self.journal, OWNER) as journal:
            head = journal.head()
            head = journal.append(event("pub"), expected_head=head)
            head = journal.append(event("private", "PRIVATE", timestamp=7), expected_head=head)
            head = journal.append(event("expired", timestamp=9, valid_to=4), expected_head=head)
            self.anchor = head
        self.index = self.dir / "derived.sqlite"

    def args(self, **overrides):
        args = {"owner": OWNER, "trusted_head": self.anchor,
                "visibility": "PUBLIC", "model": "fixed-fixture-embed/1",
                "dimension": 2, "vectors": {"pub": [1.0, 0.0]}}
        args.update(overrides)
        return args

    def test_no_canonical_write_and_no_private_projection(self):
        before = self.journal.read_bytes()
        rows = prepare_index(self.journal, **self.args())
        self.assertEqual([r[0] for r in rows], ["pub"])
        self.assertNotIn(b"NOT_FOR_DERIVED_SQLITE", repr(rows).encode())
        self.assertEqual(self.journal.read_bytes(), before)

    def test_private_requires_opt_in_and_independent_visibility(self):
        args = self.args(visibility="PRIVATE", vectors={"private": (0.0, 1.0)})
        with self.assertRaisesRegex(VectorIndexError, "opt-in"):
            prepare_index(self.journal, **args)
        rows = prepare_index(self.journal, **dict(args, allow_nonpublic=True))
        self.assertEqual([r[0] for r in rows], ["private"])
        self.assertNotIn("pub", repr(rows))
        with self.assertRaisesRegex(VectorIndexError, "unauthorized"):
            prepare_index(self.journal, **self.args(vectors={"private": [1, 0]}))

    def test_anchor_and_owner_required(self):
        with self.assertRaises(NoosphereError):
            prepare_index(self.journal, **self.args(owner="wrong-owner"))
        with self.assertRaises(NoosphereError):
            prepare_index(self.journal, **self.args(trusted_head="0" * 64))
        with self.assertRaises(VectorIndexError):
            prepare_index(self.journal, **self.args(trusted_head="malformed"))

    def test_stale_anchor_after_new_event_fails_closed(self):
        with LocalNoosphere(self.journal, OWNER) as journal:
            journal.append(event("later", timestamp=12), expected_head=journal.head())
        with self.assertRaises(NoosphereError):
            prepare_index(self.journal, **self.args())
        self.assertFalse(self.index.exists())

    def test_deleted_expired_or_unknown_ids_are_not_reindexed(self):
        with self.assertRaisesRegex(VectorIndexError, "unauthorized"):
            prepare_index(self.journal, **self.args(vectors={"expired": [0, 1]}, valid_at_ns=5))
        with self.assertRaisesRegex(VectorIndexError, "unauthorized"):
            prepare_index(self.journal, **self.args(vectors={"ghost": [0, 1]}))
        rows = prepare_index(self.journal, **self.args(valid_at_ns=5))
        self.assertEqual(len(rows), 1)

    def test_vectors_fail_closed_without_writing_files(self):
        invalid = [([1], 2), ([1, True], 2), ([1, math.nan], 2),
                   ([1, math.inf], 2), ([1, 10**999], 2),
                   ([1, "2"], 2), ([1, 1], True)]
        for values, dimension in invalid:
            with self.subTest(values=repr(values)[:100], dimension=dimension):
                with self.assertRaises(VectorIndexError):
                    prepare_index(self.journal, **self.args(dimension=dimension, vectors={"pub": values}))
        self.assertFalse(self.index.exists())

    def test_malformed_scope_and_symlink_guard(self):
        for bad in ([], False, "restricted", "PRIVATE"):
            with self.subTest(visibility=bad):
                with self.assertRaises(VectorIndexError):
                    prepare_index(self.journal, **self.args(visibility=bad))
        with self.assertRaises(VectorIndexError):
            rebuild_index(self.journal, self.journal, **self.args())
        alias = self.dir / "alias.sqlite"
        try:
            alias.symlink_to(self.journal)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaises(VectorIndexError):
            rebuild_index(self.journal, alias, **self.args())

    def test_no_mandatory_extension_and_explicit_load(self):
        try:
            current = importlib.metadata.version("sqlite-vec")
        except importlib.metadata.PackageNotFoundError:
            current = None
        if current == SQLITE_VEC_VERSION:
            self.skipTest("pinned sqlite-vec installed; exercised in integration test")
        with self.assertRaisesRegex(VectorIndexError, "sqlite-vec"):
            rebuild_index(self.journal, self.index, **self.args())
        self.assertFalse(self.index.exists())

    def test_real_vec_index_opt_in_if_installed(self):
        try:
            current = importlib.metadata.version("sqlite-vec")
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("optional sqlite-vec extra absent")
        if current != SQLITE_VEC_VERSION:
            self.skipTest("optional sqlite-vec version does not match pin")
        before = self.journal.read_bytes()
        summary = rebuild_index(self.journal, self.index, **self.args(
            vectors={"pub": [1, 0], "expired": [0, 1]}))
        self.assertEqual(summary["count"], 2)
        query = {k: v for k, v in self.args().items() if k != "vectors"}
        result = search_index(self.index, **query, query=[1, 0])
        self.assertEqual([r["event_id"] for r in result], ["pub", "expired"])
        self.assertNotIn(b"NOT_FOR_DERIVED_SQLITE", self.index.read_bytes())
        self.assertEqual(self.journal.read_bytes(), before)
        with self.assertRaises(VectorIndexError):
            search_index(self.index, **dict(query, trusted_head="0" * 64), query=[1, 0])
        with self.assertRaises(VectorIndexError):
            search_index(self.index, **dict(query, visibility="PRIVATE", allow_nonpublic=True), query=[1, 0])
        with self.assertRaises(VectorIndexError):
            rebuild_index(self.journal, self.index, **self.args())
        summary2 = rebuild_index(self.journal, self.index, replace=True,
                                 **self.args(vectors={"pub": [1, 0]}))
        self.assertEqual(summary2["count"], 1)
        self.assertEqual(len(search_index(self.index, **query, query=[1, 0])), 1)
        # Data tampering must fail even if scope metadata remains unchanged.
        with closing(sqlite3.connect(self.index)) as db:
            with db:
                db.execute("UPDATE records SET event_hash = 'tampered' WHERE event_id = 'pub'")
        with self.assertRaisesRegex(VectorIndexError, "checksum"):
            search_index(self.index, **query, query=[1, 0])
        with closing(sqlite3.connect(self.index)) as db:
            with db:
                db.execute("UPDATE metadata SET v = 'bad' WHERE k = 'source_head'")
        with self.assertRaises(VectorIndexError):
            search_index(self.index, **query, query=[1, 0])


if __name__ == "__main__":
    unittest.main()
