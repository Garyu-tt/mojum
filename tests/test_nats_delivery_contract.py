import unittest

from mojum.nats_delivery_contract import (
    ContractError, PROVENANCE, SCHEMA, admit_envelope, canonical_bytes,
    delivery_key, plan_delivery, receipt_digest,
)


def envelope(**overrides):
    value = {
        "schema": SCHEMA, "provenance": PROVENANCE, "node_id": "node-a",
        "stream": "MOJUM_EVENTS", "subject": "mojum.events.body",
        "event_id": "event-0001", "delivery_id": "delivery-0001",
        "sequence": 1, "tick": 10, "payload_sha256": "a" * 64,
        "causation_id": None,
    }
    value.update(overrides)
    return value


class NatsDeliveryContractTests(unittest.TestCase):
    def test_admits_and_detaches_exact_envelope(self):
        raw = envelope()
        admitted = admit_envelope(raw)
        raw["node_id"] = "mutated"
        self.assertEqual(admitted.node_id, "node-a")
        self.assertEqual(admitted.sequence, 1)

    def test_rejects_extra_and_missing_fields(self):
        for raw in (envelope(extra="x"), {k: v for k, v in envelope().items() if k != "tick"}):
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                admit_envelope(raw)

    def test_rejects_oversized_mapping_before_key_iteration(self):
        raw = envelope()
        raw.update({f"extra-{i}": i for i in range(10_000)})
        with self.assertRaisesRegex(ContractError, "fields do not match"):
            admit_envelope(raw)

    def test_rejects_hostile_string_subclass_key_before_hashing(self):
        class HostileKey(str):
            __hash__ = str.__hash__
            def __eq__(self, other):
                raise AssertionError("hostile key equality must not execute")
        raw = envelope()
        raw[HostileKey("node_id")] = raw.pop("node_id")
        with self.assertRaisesRegex(ContractError, "native strings"):
            admit_envelope(raw)

    def test_rejects_private_or_wrong_schema(self):
        for key, value in (("provenance", "PRIVATE"), ("schema", "mojum.nats-delivery/2")):
            with self.subTest(key=key), self.assertRaises(ContractError):
                admit_envelope(envelope(**{key: value}))

    def test_rejects_bool_and_out_of_range_counters(self):
        for value in (True, -1, 1 << 63):
            with self.subTest(value=value), self.assertRaises(ContractError):
                admit_envelope(envelope(sequence=value))

    def test_rejects_non_native_mapping_and_bad_tokens(self):
        from collections import UserDict
        with self.assertRaises(ContractError):
            admit_envelope(UserDict(envelope()))
        for value in ("", "contains space", "x" * 97, "ümlaut"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                admit_envelope(envelope(node_id=value))

    def test_rejects_bad_digest_and_causation(self):
        for changes in ({"payload_sha256": "A" * 64}, {"payload_sha256": "f" * 63},
                        {"causation_id": ""}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                admit_envelope(envelope(**changes))

    def test_dedup_key_is_stable_and_scoped(self):
        first = admit_envelope(envelope())
        self.assertEqual(delivery_key(first), delivery_key(admit_envelope(envelope())))
        self.assertNotEqual(delivery_key(first), delivery_key(admit_envelope(envelope(node_id="node-b"))))

    def test_delivery_plan_accept_gap_stale_duplicate(self):
        item = admit_envelope(envelope())
        accepted = plan_delivery(item, last_sequence=0)
        self.assertEqual((accepted.state, accepted.next_sequence), ("ACCEPT_CANDIDATE", 2))
        gap = plan_delivery(admit_envelope(envelope(sequence=3)), last_sequence=1)
        self.assertEqual((gap.state, gap.next_sequence), ("GAP", 2))
        stale = plan_delivery(item, last_sequence=1)
        self.assertEqual(stale.state, "STALE")
        duplicate = plan_delivery(item, seen_delivery_keys=[delivery_key(item)])
        self.assertEqual(duplicate.state, "DUPLICATE")

    def test_rejects_unbounded_or_malformed_seen_keys(self):
        item = admit_envelope(envelope())
        with self.assertRaises(ContractError):
            plan_delivery(item, seen_delivery_keys=["x" * 64])
        with self.assertRaises(ContractError):
            plan_delivery(item, seen_delivery_keys=["a" * 64] * 4097)

    def test_canonical_and_receipt_are_deterministic(self):
        item = admit_envelope(envelope())
        self.assertEqual(canonical_bytes(item), canonical_bytes(item))
        self.assertEqual(receipt_digest(item, "ACCEPT_CANDIDATE"),
                         receipt_digest(item, "ACCEPT_CANDIDATE"))
        self.assertNotEqual(receipt_digest(item, "ACCEPT_CANDIDATE"),
                            receipt_digest(item, "GAP"))


if __name__ == "__main__":
    unittest.main()
