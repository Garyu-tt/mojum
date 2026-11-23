"""Adversarial direct-Python identity checks for GENESIS's versioned fixture.

No PRIVATE records, time source, network, or body actuator is involved.
"""
import copy
import json
from pathlib import Path
import unittest

from mojum.genesis import (
    ACQUISITION_SCHEMA,
    GENOTYPE_SCHEMA,
    GenesisError,
    GenomeLedger,
    validate_acquisition,
    validate_seed,
)


SEED = json.loads(
    (Path(__file__).resolve().parent.parent / "examples" / "genesis_seed.json").read_text(encoding="utf-8")
)


class PretendEqual:
    """Object that silently impersonates every expected string."""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


class ExplodingEquality:
    """Object that crashes comparisons; validators must reject before calling."""

    def __eq__(self, other):
        raise AssertionError("untrusted equality must not run")

    def __ne__(self, other):
        raise AssertionError("untrusted inequality must not run")


class PretendString(str):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


def event():
    return {
        "schema": ACQUISITION_SCHEMA,
        "event_id": "strict-identity-test",
        "genome_id": SEED["genome_id"],
        "gene": "heartbeat_bpm",
        "delta": 1,
        "valid_from_tick": 1,
        "observed_at_ns": 100,
        "recorded_at_ns": 101,
        "source": "test:synthetic",
        "evidence": "fixture",
    }


class StrictGenesisIdentityTests(unittest.TestCase):
    def test_seed_schema_rejects_forged_and_exploding_equality(self):
        for bad in (PretendEqual(), ExplodingEquality(), PretendString(GENOTYPE_SCHEMA)):
            with self.subTest(kind=type(bad).__name__):
                seed = copy.deepcopy(SEED)
                seed["schema"] = bad
                with self.assertRaises(GenesisError):
                    validate_seed(seed)

    def test_seed_genome_id_rejects_string_subclass_with_custom_equality(self):
        seed = copy.deepcopy(SEED)
        seed["genome_id"] = PretendString(SEED["genome_id"])
        with self.assertRaises(GenesisError):
            GenomeLedger(seed)

    def test_acquisition_schema_rejects_forged_and_exploding_equality(self):
        for bad in (PretendEqual(), ExplodingEquality(), PretendString(ACQUISITION_SCHEMA)):
            with self.subTest(kind=type(bad).__name__):
                ledger = GenomeLedger(SEED)
                before = ledger.revision
                item = event()
                item["schema"] = bad
                with self.assertRaises(GenesisError):
                    ledger.append(item, before)
                self.assertEqual(ledger.snapshot()["events"], [])
                self.assertEqual(ledger.revision, before)

    def test_foreign_genome_object_rejected_before_ledger_mutation(self):
        for bad in (PretendEqual(), ExplodingEquality(), PretendString(SEED["genome_id"])):
            with self.subTest(kind=type(bad).__name__):
                ledger = GenomeLedger(SEED)
                before = ledger.revision
                item = event()
                item["genome_id"] = bad
                with self.assertRaises(GenesisError):
                    ledger.append(item, before)
                self.assertEqual(ledger.snapshot()["events"], [])
                self.assertEqual(ledger.revision, before)

    def test_acquisition_direct_api_rejects_forged_expected_owner(self):
        for bad in (PretendEqual(), ExplodingEquality()):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(GenesisError):
                    validate_acquisition(event(), bad)

    def test_acquisition_gene_requires_plain_string(self):
        ledger = GenomeLedger(SEED)
        item = event()
        item["gene"] = PretendString("heartbeat_bpm")
        with self.assertRaises(GenesisError):
            ledger.append(item, ledger.revision)
        self.assertEqual(ledger.snapshot()["events"], [])

    def test_snapshot_schema_rejects_forged_and_exploding_equality(self):
        good = GenomeLedger(SEED).snapshot()
        for bad in (PretendEqual(), ExplodingEquality(), PretendString(good["schema"])):
            with self.subTest(kind=type(bad).__name__):
                snapshot = copy.deepcopy(good)
                snapshot["schema"] = bad
                with self.assertRaises(GenesisError):
                    GenomeLedger.from_snapshot(snapshot)

    def test_snapshot_checksum_rejects_forged_equality(self):
        ledger = GenomeLedger(SEED)
        ledger.append(event(), ledger.revision)
        good = ledger.snapshot()
        for bad in (PretendEqual(), ExplodingEquality(), PretendString(good["revision"])):
            with self.subTest(kind=type(bad).__name__):
                snapshot = copy.deepcopy(good)
                snapshot["revision"] = bad
                with self.assertRaises(GenesisError):
                    GenomeLedger.from_snapshot(snapshot)

    def test_snapshot_checksum_rejects_malformed_hex(self):
        good = GenomeLedger(SEED).snapshot()
        for bad in ("", "g" * 64, "A" * 64, "0" * 63, True):
            with self.subTest(bad=bad):
                snapshot = copy.deepcopy(good)
                snapshot["revision"] = bad
                with self.assertRaises(GenesisError):
                    GenomeLedger.from_snapshot(snapshot)

    def test_noncanonical_dict_keys_rejected_before_lookup_or_equality(self):
        # String subclasses can compare/hash exactly like the expected field
        # names and silently survive a JSON/dict equality round-trip.
        for location in ("seed", "inherited", "acquisition", "snapshot"):
            with self.subTest(location=location):
                seed = copy.deepcopy(SEED)
                if location == "seed":
                    value = seed.pop("schema")
                    seed[PretendString("schema")] = value
                    with self.assertRaises(GenesisError):
                        validate_seed(seed)
                elif location == "inherited":
                    value = seed["inherited"].pop("heartbeat_bpm")
                    seed["inherited"][PretendString("heartbeat_bpm")] = value
                    with self.assertRaises(GenesisError):
                        validate_seed(seed)
                elif location == "acquisition":
                    item = event()
                    value = item.pop("event_id")
                    item[PretendString("event_id")] = value
                    ledger = GenomeLedger(seed)
                    before = ledger.revision
                    with self.assertRaises(GenesisError):
                        ledger.append(item, before)
                    self.assertEqual(ledger.revision, before)
                else:
                    snapshot = GenomeLedger(seed).snapshot()
                    value = snapshot.pop("revision")
                    snapshot[PretendString("revision")] = value
                    with self.assertRaises(GenesisError):
                        GenomeLedger.from_snapshot(snapshot)

    def test_nonbuiltin_dict_subclasses_rejected(self):
        class ForgedDict(dict):
            pass

        with self.assertRaises(GenesisError):
            validate_seed(ForgedDict(copy.deepcopy(SEED)))
        with self.assertRaises(GenesisError):
            validate_seed({**copy.deepcopy(SEED),
                           "inherited": ForgedDict(SEED["inherited"])})
        with self.assertRaises(GenesisError):
            validate_acquisition(ForgedDict(event()), SEED["genome_id"])
        with self.assertRaises(GenesisError):
            GenomeLedger.from_snapshot(ForgedDict(GenomeLedger(SEED).snapshot()))

    def test_valid_seed_acquisition_and_snapshot_remain_compatible(self):
        ledger = GenomeLedger(SEED)
        ledger.append(event(), ledger.revision)
        snap = ledger.snapshot()
        reopened = GenomeLedger.from_snapshot(copy.deepcopy(snap))
        self.assertEqual(reopened.revision, ledger.revision)
        self.assertEqual(reopened.snapshot(), snap)
        self.assertEqual(reopened.phenotype(1), ledger.phenotype(1))


if __name__ == "__main__":
    unittest.main()
