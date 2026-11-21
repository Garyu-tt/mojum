"""Regression: registry validation must preserve one-shot policy iterables."""

import unittest

from mojum.policy_identity import (
    POLICIES, PolicyIdentity, PolicyIdentityError, validate_identities,
)


def identity(name):
    return PolicyIdentity(
        inspection_id=name,
        selectors=(("mode", name),),
        state_schema="state/" + name,
        learning_schema="learning/" + name,
        intent_source="source/" + name,
        configuration="mode=" + name,
    )


class OneShotRegistryTests(unittest.TestCase):
    def test_generator_returns_all_checked_declarations_in_order(self):
        expected = (identity("first"), identity("second"))
        actual = validate_identities(item for item in expected)
        self.assertIsInstance(actual, tuple)
        self.assertEqual(actual, expected)
        self.assertIs(actual[0], expected[0])
        self.assertIs(actual[1], expected[1])

    def test_duplicate_in_generator_still_fails_closed(self):
        def declarations():
            yield identity("same")
            yield identity("same")

        with self.assertRaisesRegex(PolicyIdentityError, "duplicate policy inspection id"):
            validate_identities(declarations())

    def test_empty_generator_is_explicitly_empty(self):
        self.assertEqual(validate_identities(item for item in ()), ())

    def test_shipped_registry_is_not_changed(self):
        self.assertGreater(len(POLICIES), 0)
        self.assertEqual(validate_identities(iter(POLICIES)), POLICIES)


if __name__ == "__main__":
    unittest.main()
