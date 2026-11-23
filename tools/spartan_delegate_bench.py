#!/usr/bin/env python3
"""Reproducible stdlib-only 300-lease delegation ledger microbenchmark (#701)."""
from __future__ import annotations

import json
import time
import tracemalloc

from tools import spartan_coordinator as sc
from tools import spartan_delegate as sd


def run(count: int = 300) -> dict:
    if type(count) is not int or not 1 <= count <= 300:
        raise ValueError("count must be 1..300")
    db = sc.connect(":memory:")
    try:
        sc.create_schema(db)
        sc.init(db, count, 100)
        sd.create_schema(db)
        sc.enqueue(db, [{"id": f"bench-{i:03d}", "scope": f"bench.scope.{i:03d}"}
                        for i in range(count)], 101)
        started = time.perf_counter()
        tracemalloc.start()
        try:
            for worker in range(1, count + 1):
                claim = sc.claim(db, worker, 110, 900)
                if claim is None:
                    raise RuntimeError("coordinator did not grant expected unique lease")
                ticket = sd.submit(db, claim["id"], worker, claim["attempts"], 111)
                sd.start(db, ticket, 112)
                sd.report(db, ticket, 113, "a" * 64)
                if not sd.prepare_pr_intent(db, ticket, 114, "a" * 64):
                    raise RuntimeError("unexpected duplicate intent")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return {"schema": sd.SCHEMA, "jobs": count,
                "benchmark": "in-memory-fenced-ledger-no-model-no-network",
                "seconds": round(time.perf_counter() - started, 4),
                "python_tracemalloc_peak_bytes": peak,
                "intent_count": db.execute(
                    "SELECT COUNT(*) FROM delegated_pr_intents").fetchone()[0]}
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
