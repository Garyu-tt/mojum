"""One public name must never silently mean two persisted contracts (#116).

Issue #116 is a release blocker because two unreleased draft runtimes both
answered to ``local_cell_v2`` while pinning incompatible SQLite schemas.
The registry is the single source of truth the read-only wire inspector and
the CLI consume; this module proves duplicates fail closed and that the two
historical drafts stay explicitly distinct.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from mojum import aquarium, cell_policy_v2, cell_policy_v3, cell_policy_v4, developmental
from mojum.epoch_sense_v2 import EPOCH_TICKS, MAX_ABSOLUTE_TICKS, SCHEMA as EPOCH_SENSE_SCHEMA
from mojum.archived_policy_v2a import SCHEMA as V2A_SCHEMA
from mojum.policy_identity import (
    POLICIES, PolicyIdentity, PolicyIdentityError, by_selector,
    identities_mentioning_value, validate_identities,
)


ROOT = Path(__file__).resolve().parents[1]
_A = "mojum.developmental.explorer.local-cell/2"
_B = "mojum.developmental.local-cell/2"


class RegistryShapeTests(unittest.TestCase):
    def test_every_inspection_identity_is_unique(self):
        self.assertEqual(len(POLICIES), 8)
        for field in (
            "inspection_id", "configuration", "state_schema",
            "learning_schema", "intent_source",
        ):
            values = [getattr(item, field) for item in POLICIES if getattr(item, field) is not None]
            self.assertEqual(len(values), len(set(values)), field)
        selector_sets = [tuple(sorted(item.selector_dict.items())) for item in POLICIES]
        self.assertEqual(len(selector_sets), len(set(selector_sets)))

    def test_registry_matches_the_runtime_module_constants(self):
        by_id = {item.inspection_id: item for item in POLICIES}
        self.assertEqual(by_id["local_cell_v2_a_unreleased"].state_schema, V2A_SCHEMA)
        self.assertEqual(by_id["local_cell_v2_b_unreleased"].state_schema,
                         cell_policy_v2.SCHEMA)
        self.assertEqual(by_id["local_cell_v2_b_unreleased"].learning_schema,
                         cell_policy_v2.LEARNING_SCHEMA)
        self.assertEqual(by_id["local_cell_v3"].state_schema, cell_policy_v3.SCHEMA)
        self.assertEqual(by_id["local_cell_v3"].learning_schema,
                         cell_policy_v3.LEARNING_SCHEMA)
        self.assertEqual(by_id["local_cell_v4"].state_schema, cell_policy_v4.SCHEMA)
        self.assertEqual(by_id["local_cell_v4"].learning_schema,
                         cell_policy_v4.LEARNING_SCHEMA)
        self.assertEqual(by_id["local_cell_v4"].selector_dict, {
            "exploration_mode": "goal_free",
            "developmental_schema": cell_policy_v4.SCHEMA,
            "developmental_policy": "local_cell_v4",
            "epoch_transition_schema": aquarium.EPOCH_EVENT_SCHEMA,
            "epoch_ticks": EPOCH_TICKS,
            "proprioception_schema": EPOCH_SENSE_SCHEMA,
            "max_absolute_ticks": MAX_ABSOLUTE_TICKS,
        })
        self.assertEqual(by_id["motor_play_v1"].state_schema, developmental.SCHEMA)

    def test_public_modes_and_archived_constant_cannot_collide(self):
        modes = (aquarium.V2A_ARCHIVE_MODE, aquarium.V2B_MODE,
                 aquarium.V2B_WIRE_MODE, aquarium.V3_MODE, aquarium.V4_MODE)
        self.assertEqual(len(set(modes)), len(modes))
        # The archived module used to export the canonical ``local_cell_v2``
        # name; that is exactly the impersonation #116 blocks.
        from mojum import archived_policy_v2a
        self.assertEqual(archived_policy_v2a.MODE, aquarium.V2A_ARCHIVE_MODE)
        self.assertNotEqual(archived_policy_v2a.MODE, aquarium.V2B_WIRE_MODE)


class TwoDraftsTests(unittest.TestCase):
    def test_same_word_two_contracts(self):
        drafts = identities_mentioning_value("local_cell_v2")
        self.assertEqual(
            sorted(item.inspection_id for item in drafts),
            ["local_cell_v2_a_unreleased", "local_cell_v2_b_unreleased"],
        )
        a, b = drafts
        self.assertNotEqual(a.state_schema, b.state_schema)
        self.assertNotEqual(a.learning_schema, b.learning_schema)
        self.assertNotEqual(a.intent_source, b.intent_source)
        # Same word, different configuration key: neither is canonical.
        self.assertEqual(a.selector_dict["motor_policy"], "local_cell_v2")
        self.assertEqual(b.selector_dict["developmental_policy"], "local_cell_v2")
        self.assertNotEqual(a.selector_dict, b.selector_dict)

    def test_selector_resolution_is_exact_and_fail_closed(self):
        a = by_selector({
            "exploration_mode": "goal_free", "developmental_schema": _A,
            "motor_policy": "local_cell_v2", "motor_policy_schema": _A,
        })
        b = by_selector({
            "exploration_mode": "goal_free", "developmental_schema": _B,
            "developmental_policy": "local_cell_v2",
        })
        self.assertEqual(a.inspection_id, "local_cell_v2_a_unreleased")
        self.assertEqual(b.inspection_id, "local_cell_v2_b_unreleased")
        self.assertEqual(by_selector({}).inspection_id, "scripted_v1")

        mixed = {
            "exploration_mode": "goal_free", "developmental_schema": _B,
            "motor_policy": "local_cell_v2", "motor_policy_schema": _A,
        }
        for selectors in (mixed, {"developmental_policy": "local_cell_v3"},
                          {"exploration_mode": "goal_free"}):
            with self.subTest(selectors=selectors):
                with self.assertRaisesRegex(PolicyIdentityError,
                                            "ambiguous or unknown"):
                    by_selector(selectors)

    def test_validation_rejects_duplicate_or_incomplete_identities(self):
        good = PolicyIdentity(
            inspection_id="x", selectors=(("k", "v"),), state_schema="s",
            learning_schema="l", intent_source="i", configuration="k=v",
        )
        with self.assertRaisesRegex(PolicyIdentityError, "inspection id"):
            validate_identities((good, PolicyIdentity(
                inspection_id="x", selectors=(("k2", "v2"),), state_schema="s2",
                learning_schema="l2", intent_source="i2", configuration="k2=v2",
            )))
        duplicate_selectors = PolicyIdentity(
            inspection_id="y", selectors=(("k", "v"),), state_schema="s2",
            learning_schema="l2", intent_source="i2", configuration="k2=v2",
        )
        with self.assertRaisesRegex(PolicyIdentityError, "selectors"):
            validate_identities((good, duplicate_selectors))
        duplicate_contract = PolicyIdentity(
            inspection_id="z", selectors=(("k", "other"),), state_schema="s",
            learning_schema="l", intent_source="i", configuration="k=other",
        )
        with self.assertRaisesRegex(PolicyIdentityError, "persisted contract"):
            validate_identities((good, duplicate_contract))
        incomplete = PolicyIdentity(
            inspection_id="w", selectors=(("q", "v"),), state_schema=None,
            learning_schema="l", intent_source=None, configuration="q=v",
        )
        with self.assertRaisesRegex(PolicyIdentityError, "incomplete"):
            validate_identities((incomplete,))
        with self.assertRaisesRegex(PolicyIdentityError, "configuration"):
            validate_identities((good, PolicyIdentity(
                inspection_id="v", selectors=(("k3", "v3"),), state_schema="s3",
                learning_schema="l3", intent_source="i3", configuration="k=v",
            )))


class ListPoliciesCliTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "mojum.aquarium_wire", *args],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        )

    def test_listing_works_without_a_database(self):
        done = self._run("--list-policies")
        self.assertEqual(done.returncode, 0, done.stderr)
        payload = json.loads(done.stdout)
        self.assertEqual(payload["schema"], "mojum.aquarium.policy-identities/1")
        ids = [row["inspection_id"] for row in payload["identities"]]
        self.assertEqual(ids, [item.inspection_id for item in POLICIES])
        v2a, v2b = (row for row in payload["identities"]
                    if row["inspection_id"].startswith("local_cell_v2_"))
        self.assertEqual(v2a["selectors"], {
            "exploration_mode": "goal_free", "developmental_schema": _A,
            "motor_policy": "local_cell_v2", "motor_policy_schema": _A,
        })
        self.assertEqual(v2b["selectors"], {
            "exploration_mode": "goal_free", "developmental_schema": _B,
            "developmental_policy": "local_cell_v2",
        })
        self.assertNotEqual(v2a["state_schema"], v2b["state_schema"])

    def test_inspection_still_requires_a_database(self):
        done = self._run()
        self.assertEqual(done.returncode, 2)
        self.assertIn("--db", done.stderr)


if __name__ == "__main__":
    unittest.main()
