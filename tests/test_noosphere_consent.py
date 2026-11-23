import copy
from collections import UserDict
import hashlib
import json
import unittest
from unittest.mock import patch

from mojum.noosphere_consent import (
    AUTHORIZED_SCHEMA, CONSENT_SCHEMA, ConsentError, authorize_dataset,
    validate_grant,
)


def digest(value):
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def event(*, event_id="evt-1", source="sensor-a", revision="rev-1",
          topic="observation", visibility="PUBLIC", valid_from=0,
          valid_to=None, agent_id="dante"):
    return {
        "schema": "mojum.noosphere.event/1",
        "event_id": event_id,
        "agent_id": agent_id,
        "topic": topic,
        "observed_at_ns": 10,
        "recorded_at_ns": 11,
        "valid_from_ns": valid_from,
        "valid_to_ns": valid_to,
        "visibility": visibility,
        "source": source,
        "source_revision": revision,
        "payload": {"value": 7},
    }


def dataset(events):
    projection = {
        "schema": "mojum.noosphere.dataset/1",
        "agent_id": "dante",
        "filters": {
            "topic": None, "source": None, "as_of_recorded_ns": None,
            "valid_at_ns": None, "include_nonpublic": True, "limit": 100,
        },
        "items": [
            {"seq": index, "event": item, "event_hash": f"{index:064x}"}
            for index, item in enumerate(events, 1)
        ],
    }
    return {"projection": projection, "sha256": digest(projection)}

 
def resign(source):
    """Recompute the projection digest after deliberately changing a fixture."""
    source["sha256"] = digest(source["projection"])
    return source


def grant(*, consent_id="consent-1", source="sensor-a", revision="rev-1",
          topics=None, visibility=None, valid_from=0, valid_to=100,
          purpose="research", agent_id="dante"):
    return {
        "schema": CONSENT_SCHEMA,
        "consent_id": consent_id,
        "agent_id": agent_id,
        "source": source,
        "source_revision": revision,
        "topics": sorted(topics if topics is not None else ["observation"]),
        "allowed_visibility": sorted(visibility if visibility is not None else ["PUBLIC"]),
        "valid_from_ns": valid_from,
        "valid_to_ns": valid_to,
        "purpose": purpose,
        "granted_by": "fixture-owner",
    }


class NoosphereConsentTests(unittest.TestCase):
    def test_exact_scope_authorizes_only_matching_event(self):
        source = dataset([
            event(),
            event(event_id="evt-2", source="other-source"),
            event(event_id="evt-3", revision="rev-2"),
            event(event_id="evt-4", topic="private-note"),
            event(event_id="evt-5", visibility="PRIVATE"),
        ])
        result = authorize_dataset(
            source, [grant()], as_of_ns=50, purpose="research",
        )
        self.assertEqual(result["schema"], AUTHORIZED_SCHEMA)
        self.assertEqual([item["event"]["event_id"] for item in result["items"]],
                         ["evt-1"])

    def test_private_visibility_requires_explicit_private_grant(self):
        source = dataset([event(visibility="PRIVATE")])
        public_only = authorize_dataset(
            source, [grant()], as_of_ns=50, purpose="research",
        )
        private_allowed = authorize_dataset(
            source, [grant(visibility=["PRIVATE"])],
            as_of_ns=50, purpose="research",
        )
        self.assertEqual(public_only["items"], [])
        self.assertEqual(len(private_allowed["items"]), 1)

    def test_expired_and_not_yet_valid_grants_do_not_authorize(self):
        source = dataset([event()])
        expired = authorize_dataset(
            source, [grant(valid_to=50)], as_of_ns=50, purpose="research",
        )
        future = authorize_dataset(
            source, [grant(valid_from=51)], as_of_ns=50, purpose="research",
        )
        self.assertEqual(expired["items"], [])
        self.assertEqual(future["items"], [])

    def test_event_validity_window_is_half_open(self):
        source = dataset([event(valid_from=10, valid_to=20)])
        at_start = authorize_dataset(
            source, [grant(valid_from=0, valid_to=100)],
            as_of_ns=10, purpose="research",
        )
        at_end = authorize_dataset(
            source, [grant(valid_from=0, valid_to=100)],
            as_of_ns=20, purpose="research",
        )
        self.assertEqual(len(at_start["items"]), 1)
        self.assertEqual(at_end["items"], [])

    def test_purpose_mismatch_fails_closed(self):
        result = authorize_dataset(
            dataset([event()]), [grant(purpose="training")],
            as_of_ns=50, purpose="research",
        )
        self.assertEqual(result["items"], [])

    def test_grant_is_detached(self):
        original = grant()
        result = validate_grant(original)
        result["topics"].append("mutated")
        self.assertEqual(original["topics"], ["observation"])

    def test_result_is_deterministic(self):
        source = dataset([event()])
        first = authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")
        second = authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")
        self.assertEqual(first, second)

    def test_result_does_not_alias_input(self):
        source = dataset([event()])
        result = authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")
        result["items"][0]["event"]["payload"]["value"] = 99
        self.assertEqual(source["projection"]["items"][0]["event"]["payload"]["value"], 7)

    def test_projection_digest_mismatch_rejected(self):
        source = dataset([event()])
        source["projection"]["items"][0]["event"]["payload"]["value"] = 8
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_unordered_or_duplicate_sequences_rejected(self):
        source = dataset([event(), event(event_id="evt-2")])
        source["projection"]["items"][1]["seq"] = 1
        source["sha256"] = digest(source["projection"])
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_duplicate_event_ids_rejected(self):
        source = dataset([event(), event(event_id="evt-1")])
        source["sha256"] = digest(source["projection"])
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_duplicate_consent_ids_rejected(self):
        source = dataset([event()])
        with self.assertRaises(ConsentError):
            authorize_dataset(
                source, [grant(), grant()], as_of_ns=50, purpose="research",
            )

    def test_noncanonical_visibility_list_rejected(self):
        bad = grant(visibility=["PUBLIC", "PRIVATE"])
        with self.assertRaises(ConsentError):
            validate_grant(bad)

    def test_unknown_visibility_rejected(self):
        bad = grant(visibility=["TOP SECRET"])
        with self.assertRaises(ConsentError):
            validate_grant(bad)

    def test_boolean_timestamp_rejected(self):
        bad = grant()
        bad["valid_from_ns"] = True
        with self.assertRaises(ConsentError):
            validate_grant(bad)

    def test_reversed_grant_window_rejected(self):
        with self.assertRaises(ConsentError):
            validate_grant(grant(valid_from=20, valid_to=20))

    def test_missing_grant_rejects_all_items(self):
        result = authorize_dataset(
            dataset([event()]), [grant(source="another-source")],
            as_of_ns=50, purpose="research",
        )
        self.assertEqual(result["items"], [])

    def test_invalid_input_limits_rejected(self):
        source = dataset([event()])
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [], as_of_ns=50, purpose="research")
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=True, purpose="research")

    def test_missing_and_extra_grant_fields_rejected(self):
        missing = grant()
        del missing["granted_by"]
        extra = grant()
        extra["wildcard"] = True
        for value in (missing, extra):
            with self.subTest(value=value):
                with self.assertRaises(ConsentError):
                    validate_grant(value)

    def test_non_json_mapping_implementations_rejected(self):
        with self.assertRaises(ConsentError):
            validate_grant(UserDict(grant()))

    def test_non_json_payload_rejected(self):
        source = dataset([event()])
        source["projection"]["items"][0]["event"]["payload"] = {"n": float("nan")}
        # Keep the original digest: canonicalization must reject NaN before
        # comparing the supplied projection hash.
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_oversized_payload_rejected_before_json_serialization(self):
        source = dataset([event()])
        source["projection"]["items"][0]["event"]["payload"] = {
            "blob": "x" * (16 * 1024 * 1024 + 1)
        }
        with patch(
            "mojum.noosphere_consent.json.dumps",
            side_effect=AssertionError("JSON serialization must not run"),
        ):
            with self.assertRaises(ConsentError):
                authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_nested_non_native_json_values_rejected(self):
        for payload in ({"value": (1, 2)}, {1: "non-string key"}):
            source = dataset([event()])
            source["projection"]["items"][0]["event"]["payload"] = payload
            source["sha256"] = digest(source["projection"])
            with self.subTest(payload=payload), self.assertRaises(ConsentError):
                authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_declared_topic_filter_is_enforced(self):
        source = dataset([event()])
        source["projection"]["filters"]["topic"] = "different-topic"
        resign(source)
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_declared_source_filter_is_enforced(self):
        source = dataset([event()])
        source["projection"]["filters"]["source"] = "different-source"
        resign(source)
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_declared_recorded_time_filter_is_enforced(self):
        source = dataset([event()])
        source["projection"]["filters"]["as_of_recorded_ns"] = 10
        resign(source)
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_declared_validity_filter_is_enforced(self):
        source = dataset([event(valid_from=20)])
        source["projection"]["filters"]["valid_at_ns"] = 10
        resign(source)
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [grant()], as_of_ns=50, purpose="research")

    def test_public_only_dataset_filter_rejects_private_event_even_with_grant(self):
        source = dataset([event(visibility="PRIVATE")])
        source["projection"]["filters"]["include_nonpublic"] = False
        resign(source)
        private_grant = grant(visibility=["PRIVATE"])
        with self.assertRaises(ConsentError):
            authorize_dataset(source, [private_grant], as_of_ns=50, purpose="research")

    def test_caller_mapping_is_not_mutated(self):
        original = dataset([event()])
        snapshot = copy.deepcopy(original)
        authorize_dataset(original, [grant()], as_of_ns=50, purpose="research")
        self.assertEqual(original, snapshot)


if __name__ == "__main__":
    unittest.main()
