import unittest

from mojum.qa_escalation_policy import (
    SCHEMA, EscalationInputError, decide_escalation,
)


def sample(**changes):
    value = {
        "schema": SCHEMA,
        "pr_number": 42,
        "head_sha": "a" * 40,
        "confidence_milli": 900,
        "risk_flags": [],
        "qa_verdicts": ["PASS"],
    }
    value.update(changes)
    return value


class EscalationPolicyTests(unittest.TestCase):
    def test_low_risk_stays_local(self):
        result = decide_escalation(sample())
        self.assertEqual(result.route, "LOCAL_REVIEW")
        self.assertEqual(result.reasons, ())

    def test_security_sensitive_escalates(self):
        result = decide_escalation(sample(risk_flags=["security_sensitive"]))
        self.assertEqual(result.route, "ESCALATE")
        self.assertIn("SECURITY_SENSITIVE", result.reasons)

    def test_unsafe_input_escalates(self):
        self.assertEqual(
            decide_escalation(sample(risk_flags=["unsafe_input"])).route,
            "ESCALATE",
        )

    def test_schema_migration_escalates(self):
        self.assertEqual(
            decide_escalation(sample(risk_flags=["schema_migration"])).route,
            "ESCALATE",
        )

    def test_conflicting_verdicts_escalate(self):
        result = decide_escalation(sample(qa_verdicts=["PASS", "FAIL"]))
        self.assertEqual(result.route, "ESCALATE")
        self.assertIn("CONFLICTING_QA_VERDICTS", result.reasons)

    def test_low_confidence_escalates_at_boundary(self):
        self.assertEqual(
            decide_escalation(sample(confidence_milli=599)).route,
            "ESCALATE",
        )
        self.assertEqual(
            decide_escalation(sample(confidence_milli=600)).route,
            "LOCAL_REVIEW",
        )

    def test_ambiguous_authorization_blocks(self):
        result = decide_escalation(sample(risk_flags=["authorization_ambiguous"]))
        self.assertEqual(result.route, "HUMAN_BLOCK")
        self.assertIn("AUTHORIZATION_AMBIGUOUS", result.reasons)

    def test_stale_revision_blocks_even_with_other_risks(self):
        result = decide_escalation(sample(
            risk_flags=["stale_revision", "security_sensitive"]
        ))
        self.assertEqual(result.route, "HUMAN_BLOCK")

    def test_human_block_precedes_escalation(self):
        result = decide_escalation(sample(
            confidence_milli=100,
            risk_flags=["authorization_ambiguous", "unsafe_input"],
        ))
        self.assertEqual(result.route, "HUMAN_BLOCK")
        self.assertIn("LOW_TRIAGE_CONFIDENCE", result.reasons)

    def test_receipts_are_deterministic_and_redacted(self):
        value = sample()
        first = decide_escalation(value)
        second = decide_escalation(dict(value))
        self.assertEqual(first, second)
        self.assertEqual(len(first.input_digest), 64)
        self.assertEqual(len(first.receipt_digest), 64)
        self.assertFalse(hasattr(first, "raw_input"))

    def test_input_mutation_does_not_change_existing_receipt(self):
        value = sample()
        result = decide_escalation(value)
        value["risk_flags"].append("security_sensitive")
        self.assertEqual(result.route, "LOCAL_REVIEW")

    def test_bool_is_not_accepted_as_integer(self):
        with self.assertRaises(EscalationInputError):
            decide_escalation(sample(pr_number=True))

    def test_unknown_keys_fail_closed(self):
        value = sample()
        value["model_prompt"] = "must not be admitted"
        with self.assertRaises(EscalationInputError):
            decide_escalation(value)

    def test_unknown_flags_fail_closed(self):
        with self.assertRaises(EscalationInputError):
            decide_escalation(sample(risk_flags=["network_access"]))

    def test_duplicate_flags_rejected(self):
        with self.assertRaises(EscalationInputError):
            decide_escalation(sample(risk_flags=["unsafe_input", "unsafe_input"]))

    def test_bounds_and_malformed_values_rejected(self):
        for changes in (
            {"confidence_milli": True},
            {"confidence_milli": 1001},
            {"head_sha": "A" * 40},
            {"qa_verdicts": ["PASS", "IGNORE"]},
            {"risk_flags": ["x" * 10000]},
            {"risk_flags": ["unsafe_input"] * 33},
            {"qa_verdicts": ["PASS"] * 9},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(EscalationInputError):
                    decide_escalation(sample(**changes))

    def test_non_dict_and_bool_verdict_fail(self):
        with self.assertRaises(EscalationInputError):
            decide_escalation([])
        with self.assertRaises(EscalationInputError):
            decide_escalation(sample(qa_verdicts=[True]))


if __name__ == "__main__":
    unittest.main()
