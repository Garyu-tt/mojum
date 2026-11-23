"""Regression: GENESIS must reject hostile JSON object keys without executing callbacks."""

import copy
import json
from pathlib import Path
import unittest

from mojum.genesis import ACQUISITION_SCHEMA, GenesisError, GenomeLedger, validate_acquisition, validate_seed


SEED = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text(encoding="utf-8")
)


class ExplosiveKey(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        raise AssertionError("untrusted key equality executed")

    def __ne__(self, other):
        raise AssertionError("untrusted key inequality executed")


class ExplosiveDict(dict):
    def __iter__(self):
        raise AssertionError("untrusted mapping iterator executed")

    def __getitem__(self, key):
        raise AssertionError("untrusted mapping lookup executed")


def rekey(data, key):
    value = data.pop(key)
    data[ExplosiveKey(key)] = value
    return data


def acquisition():
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": "qa-hostile-key",
        "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm",
        "delta": 1,
        "valid_from_tick": 1,
        "observed_at_ns": 100,
        "recorded_at_ns": 101,
        "source": "test:synthetic",
        "evidence": "offline key regression",
    }


class NativeDictKeyBoundaryTests(unittest.TestCase):
    def test_forged_seed_keys_are_rejected_before_equality(self):
        for key in ("schema", "genome_id", "revision", "inherited"):
            with self.subTest(key=key):
                candidate = rekey(copy.deepcopy(SEED), key)
                with self.assertRaises(GenesisError):
                    validate_seed(candidate)

    def test_forged_inherited_gene_key_is_rejected(self):
        seed = copy.deepcopy(SEED)
        rekey(seed["inherited"], "heartbeat_bpm")
        with self.assertRaises(GenesisError):
            GenomeLedger(seed)

    def test_forged_acquisition_keys_reject_without_state_mutation(self):
        for key in acquisition():
            with self.subTest(key=key):
                ledger = GenomeLedger(SEED)
                before = ledger.revision
                with self.assertRaises(GenesisError):
                    ledger.append(rekey(acquisition(), key), before)
                self.assertEqual(ledger.revision, before)
                self.assertEqual(ledger.snapshot()["events"], [])

    def test_forged_snapshot_keys_rejected(self):
        snapshot = GenomeLedger(SEED).snapshot()
        for key in snapshot:
            with self.subTest(key=key):
                with self.assertRaises(GenesisError):
                    GenomeLedger.from_snapshot(rekey(copy.deepcopy(snapshot), key))

    def test_dict_subclasses_with_callbacks_are_rejected(self):
        with self.assertRaises(GenesisError):
            validate_seed(ExplosiveDict(SEED))
        with self.assertRaises(GenesisError):
            validate_acquisition(ExplosiveDict(acquisition()), SEED["genome_id"])
        with self.assertRaises(GenesisError):
            GenomeLedger.from_snapshot(ExplosiveDict(GenomeLedger(SEED).snapshot()))

    def test_original_plain_json_contract_still_round_trips(self):
        ledger = GenomeLedger(SEED)
        ledger.append(acquisition(), ledger.revision)
        self.assertEqual(GenomeLedger.from_snapshot(ledger.snapshot()).snapshot(), ledger.snapshot())


if __name__ == "__main__":
    unittest.main()
