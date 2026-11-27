"""Deterministic interleavings for the ledger publication contract."""
import hashlib
import inspect
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from unittest.mock import patch

from mojum import genesis

SEED = {"schema": genesis.GENOTYPE_SCHEMA, "genome_id": "cas-fixture",
        "revision": "r1", "inherited": {"heartbeat_bpm": 60,
        "body_scale_milli": 1000, "sensory_gain_milli": 1000}}


def event(eid="a"):
    return {"schema": genesis.ACQUISITION_SCHEMA, "event_id": eid,
            "genome_id": SEED["genome_id"], "gene": "heartbeat_bpm",
            "delta": 7, "valid_from_tick": 0, "observed_at_ns": 0,
            "recorded_at_ns": 0, "source": "fixture", "evidence": "test"}


def expected_revision(snapshot):
    payload = {"seed": snapshot["seed"], "events": snapshot["events"]}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class LedgerInterleavingTests(unittest.TestCase):
    def test_snapshot_reader_retains_captured_revision_during_append(self):
        ledger = genesis.GenomeLedger(SEED)
        old = ledger.snapshot()
        captured, resume = Event(), Event()
        main = current_thread()
        deepcopy = genesis.copy.deepcopy

        def pause(value, *args, **kwargs):
            if current_thread() is not main and value is ledger._seed:
                captured.set()
                if not resume.wait(5):
                    raise AssertionError("snapshot reader was not released")
            return deepcopy(value, *args, **kwargs)

        with patch.object(genesis.copy, "deepcopy", side_effect=pause):
            with ThreadPoolExecutor(max_workers=1) as pool:
                reading = pool.submit(ledger.snapshot)
                try:
                    self.assertTrue(captured.wait(5))
                    ledger.append(event(), old["revision"])
                finally:
                    resume.set()
                self.assertEqual(reading.result(timeout=5), old)
        latest = ledger.snapshot()
        self.assertEqual(latest["revision"], expected_revision(latest))
        self.assertEqual(len(latest["events"]), 1)

    def test_phenotype_reader_retains_captured_revision_during_append(self):
        ledger = genesis.GenomeLedger(SEED)
        old = ledger.phenotype(0)
        captured, resume = Event(), Event()
        main = current_thread()
        sha256 = genesis.sha256

        def pause(value):
            if current_thread() is not main and value is ledger._seed:
                captured.set()
                if not resume.wait(5):
                    raise AssertionError("phenotype reader was not released")
            return sha256(value)

        with patch.object(genesis, "sha256", side_effect=pause):
            with ThreadPoolExecutor(max_workers=1) as pool:
                reading = pool.submit(ledger.phenotype, 0)
                try:
                    self.assertTrue(captured.wait(5))
                    ledger.append(event(), old["ledger_revision"])
                finally:
                    resume.set()
                self.assertEqual(reading.result(timeout=5), old)
        latest = ledger.phenotype(0)
        self.assertEqual(latest["ledger_revision"], ledger.revision)
        self.assertEqual(latest["applied_acquisition_ids"], ["a"])
        self.assertEqual(latest["parameters"]["heartbeat_bpm"], 67)

    def test_interruption_after_publication_keeps_event_and_index(self):
        ledger = genesis.GenomeLedger(SEED)
        old = ledger.revision
        method = genesis.GenomeLedger.append
        lines, start = inspect.getsourcelines(method)
        after_publish = next(start + i for i, line in enumerate(lines)
                             if line.strip() == "return next_revision")

        def interrupt(frame, kind, arg):
            if frame.f_code is method.__code__ and kind == "line" and frame.f_lineno == after_publish:
                raise KeyboardInterrupt("published without response")
            return interrupt

        previous = sys.gettrace()
        try:
            sys.settrace(interrupt)
            with self.assertRaises(KeyboardInterrupt):
                ledger.append(event(), old)
        finally:
            sys.settrace(previous)
        committed = ledger.snapshot()
        self.assertEqual(committed["events"], [event()])
        self.assertEqual(committed["revision"], expected_revision(committed))
        with self.assertRaisesRegex(genesis.GenesisError, "CAS failed"):
            ledger.append(event("b"), old)
        with self.assertRaisesRegex(genesis.GenesisError, "Duplicate"):
            ledger.append(event(), committed["revision"])
        ledger.append(event("b"), committed["revision"])
        self.assertEqual(ledger.phenotype(0)["applied_acquisition_ids"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
