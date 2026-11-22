#!/usr/bin/env python3
"""Fail-closed status reconciliation for the singleton Mojum Cezar dispatcher.

Never marks work accepted and never retries uncertain dispatches. A terminal
bad run is permanently blocked. Stale waiting/running work is cancelled only
through the exact associated loopback Cezar run ID and verified before block.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

try:
    from . import cezar_admission as admission
except ImportError:
    import cezar_admission as admission

CEZAR_BASE = "http://127.0.0.1:4329/api/v1/runs"
MAX_RESPONSE = 128 * 1024
MAX_ROWS = 12
WAIT_SECONDS = 600
RUN_SECONDS = 1800
TERMINAL_BAD = frozenset(("failed", "cancelled", "error"))
READY_FOR_QA = frozenset(("waiting", "completed", "done"))
ACTIVE = frozenset(("queued", "running", "starting"))


def _url(run_id: str):
    try:
        canonical = str(uuid.UUID(run_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise admission.AdmissionError("invalid Cezar run identifier") from exc
    if canonical != run_id:
        raise admission.AdmissionError("noncanonical Cezar run identifier")
    return CEZAR_BASE + "/" + canonical


def _request(run_id: str, method="GET", *, opener=None):
    if method not in ("GET", "POST"):
        raise admission.AdmissionError("unsupported Cezar operation")
    url = _url(run_id) + ("/cancel" if method == "POST" else "")
    req = urllib.request.Request(url, method=method)
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                   admission.NoRedirect())
    try:
        with opener.open(req, timeout=8) as response:
            if response.status != 200 or response.geturl() != url:
                raise admission.AdmissionError("unexpected Cezar response")
            raw = response.read(MAX_RESPONSE + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise admission.AdmissionError("Cezar API unavailable") from exc
    if len(raw) > MAX_RESPONSE:
        raise admission.AdmissionError("Cezar response too large")
    try:
        obj = json.loads(raw, object_pairs_hook=admission._strict_pairs,
                         parse_constant=admission._reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise admission.AdmissionError("bad Cezar run response") from exc
    if not isinstance(obj, dict):
        raise admission.AdmissionError("unexpected Cezar JSON object")
    if method == "GET" and obj.get("id") != run_id:
        raise admission.AdmissionError("Cezar run identity mismatch")
    if method == "POST" and obj.get("cancelled") is not True:
        raise admission.AdmissionError("Cezar refused cancellation")
    return obj


def _age_seconds(value: str, now: datetime):
    if not isinstance(value, str):
        raise admission.AdmissionError("missing admission timestamp")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError("naive timestamp")
        seconds = (now - ts).total_seconds()
    except ValueError as exc:
        raise admission.AdmissionError("invalid admission timestamp") from exc
    if seconds < -60:
        raise admission.AdmissionError("admission timestamp from future")
    return max(0, seconds)


def reconcile(db: sqlite3.Connection, *, now=None, get_run=None, cancel_run=None,
              wait_seconds=WAIT_SECONDS, run_seconds=RUN_SECONDS):
    now = now or datetime.now(timezone.utc)
    get_run = get_run or (lambda rid: _request(rid))
    cancel_run = cancel_run or (lambda rid: _request(rid, method="POST"))
    if not 1 <= wait_seconds <= run_seconds <= 86400:
        raise admission.AdmissionError("invalid reconciliation thresholds")
    rows = list(db.execute("SELECT * FROM admission WHERE status IN ('claimed','submitted') ORDER BY created_at LIMIT ?", (MAX_ROWS + 1,)))
    if len(rows) > MAX_ROWS:
        raise admission.AdmissionError("too many pending admission rows")
    summary = {"schema": "mojum.cezar.reconciliation.v1",
               "inspected": len(rows), "blocked": 0, "cancelled": 0,
               "still_active": 0, "awaiting_qa": 0, "uncertain_claims": 0,
               "accepted": 0}
    for row in rows:
        age = _age_seconds(row["updated_at"], now)
        if row["status"] == "claimed":
            # Dispatch may have reached Cezar even if HTTP timed out.
            # Never requeue blindly, regardless of age.
            summary["uncertain_claims"] += 1
            continue
        run_id = row["run_id"]
        if not isinstance(run_id, str):
            raise admission.AdmissionError("submitted admission lacks run ID")
        run = get_run(run_id)
        status = run.get("status")
        if not isinstance(status, str):
            raise admission.AdmissionError("missing Cezar run status")
        if status in TERMINAL_BAD:
            admission.mark_blocked(db, row["id"], run_id)
            summary["blocked"] += 1
        elif status in ACTIVE:
            if age < run_seconds:
                summary["still_active"] += 1
            else:
                cancel_run(run_id)
                verified = get_run(run_id)
                if verified.get("status") == "cancelled":
                    admission.mark_blocked(db, row["id"], run_id)
                    summary["cancelled"] += 1
                    summary["blocked"] += 1
                else:
                    summary["still_active"] += 1
        elif status in READY_FOR_QA:
            if age < wait_seconds:
                summary["awaiting_qa"] += 1
            elif status == "waiting":
                cancel_run(run_id)
                verified = get_run(run_id)
                if verified.get("status") == "cancelled":
                    admission.mark_blocked(db, row["id"], run_id)
                    summary["cancelled"] += 1
                    summary["blocked"] += 1
                else:
                    summary["awaiting_qa"] += 1
            else:
                # A model completing is not independent acceptance.
                admission.mark_blocked(db, row["id"], run_id)
                summary["blocked"] += 1
        else:
            raise admission.AdmissionError("unrecognized Cezar run state")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, required=True)
    args = ap.parse_args(argv)
    old_umask = os.umask(0o077)
    try:
        with closing(admission.connect(args.db)) as db:
            result = reconcile(db)
    except (admission.AdmissionError, OSError, sqlite3.DatabaseError) as exc:
        ap.exit(2, "reconcile refused: " + type(exc).__name__ + "\n")
    finally:
        os.umask(old_umask)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
