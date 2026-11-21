"""R0.22 independently HMAC-pinned, physical read-only historic observer.

Purpose: observe a recovered Aquarium history without accidentally making
the quarantined database an active writer. ALL SQLite access uses
mode=ro&immutable=1 and PRAGMA query_only=ON. No journal-mode, schema
setup, WAL updates, network calls, private event payloads or auto promotion.

An HMAC receipt proves a separately selected historical head only when
the independent pinned digest and owner key remain trustworthy. It cannot
prove the latest living writer is stopped, or turn two paths into two hosts.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from .archive_root_receipt import ReceiptError, verify_owner_receipt
from .segment_archive import ArchiveError, private_directory
from .segmented_noosphere import SegmentedNoosphere, STORE_SCHEMA
from .verified_replica_restore import CUSTODY_SCHEMA
from .noosphere import NoosphereError

SCHEMA = "mojum.noosphere.quarantined-observer/1"
MAX_TOPIC_BUCKETS = 64
_CUSTODY_TRIGGERS = frozenset({
    "recovery_no_event_insert", "recovery_no_segment_insert",
    "recovery_no_custody_update", "recovery_no_custody_delete",
})


class ObserverError(ValueError):
    """Invalid sealed custody, missing owner anchor or unsafe view request."""


class QuarantinedObserver(SegmentedNoosphere):
    """SQLite-only read-only view; never call SegmentedNoosphere.__init__.

    Reuses the parent's pure SELECT count/head/verify_chain/iter_events.
    The connection is protected at the SQLite VFS layer, not just by
    application's append methods. Underlying DB must be fully checkpointed
    by R0.21 beforehand to use immutable mode safely.
    """

    def __init__(self, database, receipt, owner_key, *,
                 independently_pinned_receipt_sha256, expected_agent_id):
        if type(expected_agent_id) is not str or not expected_agent_id:
            raise ObserverError("explicit expected actor ID required")
        signed = verify_owner_receipt(
            receipt, owner_key,
            independently_pinned_receipt_sha256=independently_pinned_receipt_sha256,
        )
        if signed["agent_id"] != expected_agent_id:
            raise ObserverError("owner receipt actor differs from requested actor")
        path = Path(database)
        private_directory(path.parent)
        if (not path.is_file() or path.is_symlink()
                or any(ancestor.is_symlink() for ancestor in path.parents)):
            raise ObserverError("observer requires existing regular non-symlink SQLite")
        if os.name == "posix":
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                raise ObserverError("PRIVATE observed SQLite must be owner-only 0600")
        if (Path(str(path)+"-wal").exists()
                or Path(str(path)+"-shm").exists()):
            raise ObserverError(
                "immutable observer refuses active SQLite WAL/SHM sidecars"
            )
        self.agent_id = expected_agent_id
        self.segment_size = signed["segment_size"]
        self._receipt = signed
        self._receipt_sha256 = independently_pinned_receipt_sha256
        uri = (
            "file:" + quote(str(path.absolute()), safe="/:")
            + "?mode=ro&immutable=1"
        )
        try:
            self._db = sqlite3.connect(uri, uri=True, timeout=5.0)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA query_only=ON")
            self._db.execute("PRAGMA trusted_schema=OFF")
            self._validate_custody()
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            raise

    def _validate_custody(self):
        try:
            rows = self._db.execute(
                "SELECT schema,agent_id,source_events,pinned_source_head,"
                "pinned_receipt_sha256,state FROM recovery_custody"
            ).fetchall()
            triggers = {
                row[0] for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            if (len(rows) != 1
                    or tuple(rows[0]) != (
                        CUSTODY_SCHEMA, self.agent_id,
                        self._receipt["source_events"],
                        self._receipt["source_head"], self._receipt_sha256,
                        "QUARANTINED_READ_ONLY"
                    )):
                raise ObserverError("recovered custody differs from owner receipt")
            if not _CUSTODY_TRIGGERS.issubset(triggers):
                raise ObserverError("recovered SQLite lost quarantine triggers")
            meta = dict(self._db.execute("SELECT k,v FROM metadata"))
            if meta != {
                "schema": STORE_SCHEMA, "owner": self.agent_id,
                "segment_size": str(self.segment_size),
            }:
                raise ObserverError("recovered segmented journal schema differs")
            verified = self.verify_chain(
                external_expected_head=self._receipt["source_head"]
            )
            if (verified["count"] != self._receipt["source_events"]
                    or verified["segments"] != self._receipt["sealed_segments"]+1):
                raise ObserverError("historic journal disagrees with HMAC owner receipt")
        except sqlite3.Error as exc:
            raise ObserverError("invalid or unreadable PRIVATE quarantined SQLite") from exc

    def append(self, event, *, expected_head):
        raise ObserverError("immutable observer never permits journal writes")

    def append_many(self, events, *, expected_head):
        raise ObserverError("immutable observer never permits journal writes")

    def summary(self, *, include_private_metadata=False, max_recent=12):
        """Safely emit bounded observation metadata, never raw PRIVATE payload.

        PRIVATE topics/timestamps are themselves sensitive. They are
        suppressed by default even though SQL always uses read-only mode.
        This is not a new neural or learning evaluation: event counters
        are merely a representation of recorded synthetic activity.
        """
        if type(include_private_metadata) is not bool:
            raise ObserverError("include_private_metadata must be explicit bool")
        if type(max_recent) is not int or not 0 <= max_recent <= 64:
            raise ObserverError("max_recent must be integer between 0 and 64")
        check = self.verify_chain(
            external_expected_head=self._receipt["source_head"]
        )
        result = {
            "schema": SCHEMA, "agent_id": self.agent_id,
            "journal_events": check["count"],
            "source_head": check["head"],
            "sealed_segments": self._receipt["sealed_segments"],
            "tail_events": self._receipt["tail_events"],
            "custody": "QUARANTINED_READ_ONLY",
            "root_authenticated": "owner-hmac-with-external-pinned-digest",
            "observer_connection": "sqlite-ro-immutable-query-only",
            "privacy": (
                "private-metadata-opted-in"
                if include_private_metadata else "private-metadata-redacted"
            ),
        }
        if include_private_metadata:
            # A compromised/stress fixture can deliberately use one
            # unique topic per event. Never return unbounded topic maps.
            # Deterministic top 64 by event frequency; remaining events
            # counted separately, not dropped or mislabelled as a topic.
            counts = self._db.execute(
                "SELECT topic,COUNT(*) AS cnt FROM segmented_events "
                "GROUP BY topic ORDER BY cnt DESC, topic ASC LIMIT ?",
                (MAX_TOPIC_BUCKETS + 1,),
            ).fetchall()
            selected = counts[:MAX_TOPIC_BUCKETS]
            result["topic_counts"] = {r["topic"]: r["cnt"] for r in selected}
            result["other_topics_event_count"] = (
                check["count"] - sum(r["cnt"] for r in selected)
            )
            result["topic_buckets_truncated"] = len(counts) > MAX_TOPIC_BUCKETS
            result["recent_records"] = [
                {
                    "seq": row["seq"], "topic": row["topic"],
                    "recorded_at_ns": row["recorded_at_ns"],
                    "visibility": row["visibility"],
                }
                for row in self._db.execute(
                    "SELECT seq,topic,recorded_at_ns,visibility "
                    "FROM segmented_events ORDER BY seq DESC LIMIT ?",
                    (max_recent,),
                )
            ]
            result["record_type"] = "event_metadata_not_verified_learning_quality"
        return result
