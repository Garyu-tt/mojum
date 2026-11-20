"""Opt-in locally fenced Aquarium mutations using the single trusted SQLite registry.

A *cooperative* single-host gateway, not a security sandbox or multi-host
consensus. All callers must use this gateway for EVERY state mutation; direct
Aquarium/Noosphere access can bypass it. The registry and journal remain two
separate databases and cannot be committed atomically here.

If a journal append succeeds but registry anchoring fails, the gateway is
poisoned and reopening fails closed until explicit external reconciliation.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat

from .aquarium import Aquarium
from .cold_aquarium import export_quiescent_aquarium
from .local_authority import (
    AuthorityError, LocalAuthority, _identity, _int, _writer, _MAX_INTEGER,
)


def _existing_regular_file(path, what):
    """Trusted parent required; refuse missing and symlinked final paths."""
    path = Path(path)
    if (not path.name or not path.parent.is_dir() or path.parent.is_symlink()
            or path.is_symlink()):
        raise AuthorityError(what + " requires a trusted existing regular file")
    try:
        info = path.stat()
    except OSError as exc:
        raise AuthorityError(what + " unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink < 1:
        raise AuthorityError(what + " is not a regular file")
    return path, (info.st_dev, info.st_ino)


class GuardedAquarium:
    """Narrow gateway for the two canonical fixture mutation APIs.

    Once created, the only public supported operations are step(), acquire(),
    checkpoint(), export_cold(), and close(). Do not separately expose the
    underlying Aquarium or allow direct writes to its SQLite database.

    For each mutation, a registry BEGIN IMMEDIATE is held across both the
    admission check and underlying Aquarium.append. Only after the committed
    journal advances by precisely one event is its anchor updated. Another
    cooperative writer/freeze cannot pass admission while this transaction is
    active. This does NOT make two SQLite databases crash-atomic.
    """

    def __init__(self, db_path, registry_path, seed, graph, *,
                 writer, epoch, generation, **aquarium_kwargs):
        self._closed = True
        self._poisoned = False
        self._aq = None
        self._registry = None
        self._writer = _writer(writer)
        self._epoch = _int(epoch, "epoch", 1)
        self._generation = _int(generation, "generation", 1)
        self._organism_id = _identity(
            aquarium_kwargs.get("agent_id", "dante-fixture-001"),
        )
        self._registry_path, self._registry_inode = _existing_regular_file(
            registry_path, "authority registry",
        )
        self._db_path, self._journal_inode = _existing_regular_file(
            db_path, "Aquarium journal",
        )
        # All validation must succeed before Aquarium can replay/bootstrap.
        try:
            self._registry = LocalAuthority(self._registry_path)
            self._check_path_identity()
            with self._registry._write():
                row = self._registry._row(self._organism_id)
                self._check_admission(row)
                self._aq = Aquarium(self._db_path, seed, graph, **aquarium_kwargs)
                self._check_tip(row)
            self._closed = False
        except BaseException:
            if self._aq is not None:
                self._aq.close()
            if self._registry is not None:
                self._registry.close()
            raise

    def _check_path_identity(self):
        for path, expected, label in (
            (self._registry_path, self._registry_inode, "authority registry"),
            (self._db_path, self._journal_inode, "Aquarium journal"),
        ):
            _, current = _existing_regular_file(path, label)
            if current != expected:
                raise AuthorityError(label + " was replaced or disconnected")

    def _live(self):
        if self._closed or self._aq is None or self._registry is None:
            raise AuthorityError("guarded Aquarium is closed")
        if self._poisoned:
            raise AuthorityError("previous partial commit requires offline reconciliation")
        self._check_path_identity()

    def _check_admission(self, row):
        if (row["phase"] != "ACTIVE"
                or row["writer"] != self._writer
                or row["epoch"] != self._epoch
                or row["generation"] != self._generation
                or row["pending_writer"] is not None):
            raise AuthorityError("writer is no longer admitted by local registry")
        if row["generation"] >= _MAX_INTEGER:
            raise AuthorityError("authority generation exhausted")

    def _check_tip(self, row):
        # Inspect both actual SQLite and local deterministic replay tip.
        if (self._aq.agent_id != self._organism_id
                or self._aq.tick != row["tick"]
                or self._aq.store.count() != row["event_count"]
                or self._aq.store.head() != row["head"]
                or self._aq._known_head != row["head"]):
            raise AuthorityError("Aquarium journal differs from authority anchor")
        return row["head"], row["event_count"], row["tick"]

    def _anchor_advance(self, row, *, expected_tick):
        new_count = self._aq.store.count()
        actual_head = self._aq.store.head()
        if (new_count != row["event_count"] + 1
                or actual_head == row["head"]
                or self._aq._known_head != actual_head
                or self._aq.tick != expected_tick):
            raise AuthorityError("committed Aquarium event does not match expected advance")
        cursor = self._registry._db.execute(
            "UPDATE authority SET head=?, event_count=?, tick=?, generation=? "
            "WHERE organism_id=? AND phase='ACTIVE' AND writer=? "
            "AND epoch=? AND generation=? AND event_count=? AND head=? AND tick=?",
            (actual_head, new_count, expected_tick, self._generation + 1,
             self._organism_id, self._writer, self._epoch, self._generation,
             row["event_count"], row["head"], row["tick"]),
        )
        if cursor.rowcount != 1:
            raise AuthorityError("lost local authority CAS during journal append")

    def _mutate(self, method, *args, advance_tick, **kwargs):
        self._live()
        try:
            with self._registry._write():
                self._check_path_identity()
                row = self._registry._row(self._organism_id)
                self._check_admission(row)
                self._check_tip(row)
                # Fail closed after journal commit even if registry commit or
                # subsequent data verification fails. No auto-retry.
                self._poisoned = True
                result = method(*args, **kwargs)
                self._anchor_advance(
                    row, expected_tick=row["tick"] + (1 if advance_tick else 0),
                )
                self._check_path_identity()
            self._generation += 1
            self._poisoned = False
            return result
        except (sqlite3.Error, OSError) as exc:
            raise AuthorityError("local registry/journal unavailable; fail closed") from exc

    def step(self, *, dx=0.02, dy=0.0):
        return self._mutate(self._aq.step, dx=dx, dy=dy, advance_tick=True)

    def acquire(self, acquired):
        return self._mutate(self._aq.acquire, acquired, advance_tick=False)

    def checkpoint(self):
        self._live()
        try:
            with self._registry._write():
                self._check_path_identity()
                row = self._registry._row(self._organism_id)
                self._check_admission(row)
                self._check_tip(row)
                return self._aq.checkpoint()
        except (sqlite3.Error, OSError) as exc:
            raise AuthorityError("cannot verify current local authority") from exc

    def export_cold(self, destination):
        """Capture while registry is pinned; do not leak private events to stdout."""
        self._live()
        try:
            with self._registry._write():
                self._check_path_identity()
                row = self._registry._row(self._organism_id)
                self._check_admission(row)
                self._check_tip(row)
                bundle_manifest = export_quiescent_aquarium(self._aq, destination)
                self._check_tip(row)
                self._check_path_identity()
                return bundle_manifest
        except (sqlite3.Error, OSError) as exc:
            raise AuthorityError("cold export blocked by missing local authority") from exc

    @property
    def epoch(self):
        return self._epoch

    @property
    def generation(self):
        return self._generation

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._aq.close()
            finally:
                self._registry.close()

    def __enter__(self):
        self._live()
        return self

    def __exit__(self, *_):
        self.close()
