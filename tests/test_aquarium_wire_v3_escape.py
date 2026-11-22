"""Read-only wire inspection of ``local_cell_v3_escape`` journals.

A hash-valid SQLite chain proves the journal was written by *some* process,
not that every committed frame obeys the pinned escape contract.  The
inspector must therefore reject impossible escape counters, rest
measurements and eviction bookkeeping even when the chain verifies.

These tests forge one frame and re-append it through the real journal
writer, so the inspector sees a canonical hash chain around a noncanonical
payload.  No real PRIVATE journal is created or modified.
"""

from copy import deepcopy
from importlib.resources import files
import json
import tempfile
import unittest
from pathlib import Path

from mojum.aquarium import Aquarium, V3_ESCAPE_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.cell_policy_v3_escape import (
    LEARNING_SCHEMA as ESCAPE_LEARNING_SCHEMA,
    MAX_CONSECUTIVE_RESTS,
    REPROBE_BLOCKED_BEFORE_REST,
    SCHEMA as ESCAPE_SCHEMA,
)
from mojum.noosphere import NoosphereError


DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))


class EscapeWireInspectionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)

    def open(self, name):
        return Aquarium(
            self.dir / name, SEED, GRAPH, agent_id=SEED["genome_id"],
            motor_mode=V3_ESCAPE_MODE, neural_mode="disabled",
        )

    def forged_journal(self, name, mutate):
        """Commit three real frames, then append one chain-valid forgery."""
        path = self.dir / name
        with self.open(name) as aquarium:
            for _ in range(3):
                aquarium.step()
            frame = deepcopy(aquarium._calculate_frame(None, None)[0])
            self.assertEqual(frame["tick"], 3)
            self.assertEqual(
                frame["developmental"]["learning"]["schema"],
                ESCAPE_LEARNING_SCHEMA,
            )
            mutate(frame["developmental"])
            forged = aquarium._record(
                "aquarium.frame", "frame-000000000003", frame,
                aquarium.tick * aquarium.dt_ns, aquarium.genome.revision,
            )
            aquarium.store.append(
                forged, expected_head=aquarium.checkpoint()["journal_head"],
            )
            # The chain really is valid: rejection must come from the
            # escape contract, never from a broken hash.
            self.assertEqual(aquarium.store.verify_chain()["count"], 5)
        return path

    def test_canonical_escape_journal_is_still_inspected(self):
        with self.open("canonical.sqlite3") as aquarium:
            for _ in range(40):
                aquarium.step()
        wire = inspect_journal(self.dir / "canonical.sqlite3")
        self.assertEqual(wire["format"], V3_ESCAPE_MODE)
        self.assertEqual(wire["state_schema"], ESCAPE_SCHEMA)
        self.assertEqual(wire["frames"], 40)
        self.assertEqual(wire["events"], 41)

    def test_forged_escape_counters_are_rejected(self):
        cases = (
            ("rests-above-max", lambda motor: motor["learning"].update(
                {"consecutive_rests": MAX_CONSECUTIVE_RESTS + 1})),
            ("rests-bool-alias", lambda motor: motor["learning"].update(
                {"consecutive_rests": False})),
            ("probes-above-max", lambda motor: motor["learning"].update(
                {"consecutive_blocked_probes":
                     REPROBE_BLOCKED_BEFORE_REST + 1})),
            ("probes-bool-alias", lambda motor: motor["learning"].update(
                {"consecutive_blocked_probes": True})),
            ("zero-observations", lambda motor: motor["learning"].update(
                {"observed_outcomes": 0})),
            ("blocked-above-observed", lambda motor: motor["learning"].update(
                {"observed_outcomes": 1, "blocked_outcomes": 2,
                 "measured_blocked": True})),
            ("blocked-bool-alias", lambda motor: motor["learning"].update(
                {"blocked_outcomes": False})),
            ("updates-off-by-one", lambda motor: motor["learning"].update(
                {"total_updates": 3})),
            ("updates-bool-alias", lambda motor: motor["learning"].update(
                {"total_updates": True})),
            ("negative-evictions", lambda motor: motor["learning"].update(
                {"total_evictions": -1})),
            ("forgotten-above-updates", lambda motor: motor["learning"].update(
                {"total_forgotten_observations": 99})),
            ("evictions-above-forgotten", lambda motor: motor["learning"].update(
                {"total_evictions": 1})),
            ("evicted-without-dropped", lambda motor: motor["learning"].update(
                {"evicted_cell": "0:0", "evicted_observations": 0})),
            ("dropped-without-evicted", lambda motor: motor["learning"].update(
                {"evicted_observations": 2})),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                path = self.forged_journal(f"{label}.sqlite3", mutate)
                with self.assertRaises(NoosphereError):
                    inspect_journal(path)

    def test_forged_rest_and_action_pairing_are_rejected(self):
        def rest_blocked(motor):
            motor["intent"].update({"action": "rest", "dx": 0.0, "dy": 0.0})
            motor["learning"].update({
                "action": "rest", "measured_blocked": True,
                "blocked_outcomes": 1,
            })

        def rest_ever_blocked(motor):
            motor["intent"].update({"action": "rest", "dx": 0.0, "dy": 0.0})
            motor["learning"].update({
                "action": "rest", "measured_blocked": False,
                "blocked_outcomes": 1,
            })

        def probe_without_prior_block(motor):
            motor["learning"].update({
                "escape_probe": True, "blocked_outcomes": 0,
            })

        def mismatched_action(motor):
            motor["learning"]["action"] = (
                "y+" if motor["learning"]["action"] != "y+" else "x+"
            )

        def stale_intent_tick(motor):
            motor["intent"]["tick"] = 0

        cases = (
            ("rest-measured-blocked", rest_blocked),
            ("rest-with-blocked-history", rest_ever_blocked),
            ("probe-without-prior-block", probe_without_prior_block),
            ("learning-action-differs-from-intent", mismatched_action),
            ("intent-tick-differs-from-frame", stale_intent_tick),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                path = self.forged_journal(f"{label}.sqlite3", mutate)
                with self.assertRaises(NoosphereError):
                    inspect_journal(path)


if __name__ == "__main__":
    unittest.main()
