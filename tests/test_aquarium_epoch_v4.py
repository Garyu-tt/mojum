"""Bounded v4 epoch continuation on the synthetic CPU Aquarium.

These tests prove only a versioned SQLite/replay contract.  They do not prove
neural cognition, consciousness, physical actuation, Android reliability, or
an authenticated snapshot.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, V3_MODE, V4_MODE
from mojum.aquarium_wire import inspect_journal
from mojum.cell_policy_v4 import CellExplorerV4, CellV4Error, SCHEMA as V4_SCHEMA
from mojum.epoch_sense_v2 import (
    EPOCH_TICKS,
    EpochProprioception,
    EpochSenseError,
    MAX_ABSOLUTE_TICKS,
    SCHEMA as EPOCH_SCHEMA,
)
from mojum.local_cell_sense import Proprioception
from mojum.noosphere import NoosphereError


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
EPOCH_STRESS_ENABLED = os.environ.get("MOJUM_EPOCH_V4_STRESS") == "1"


class EpochSenseV4Tests(unittest.TestCase):
    def sense(self, tick):
        return EpochProprioception(
            tick, tick // EPOCH_TICKS, tick % EPOCH_TICKS,
            "a" * 64, .16, .16, bool(tick % 2),
        )

    def test_absolute_tick_boundary_and_old_contract_remain_distinct(self):
        for tick, epoch, offset in (
            (0, 0, 0), (EPOCH_TICKS - 1, 0, EPOCH_TICKS - 1),
            (EPOCH_TICKS, 1, 0), (EPOCH_TICKS + 1, 1, 1),
        ):
            with self.subTest(tick=tick):
                cue = self.sense(tick)
                self.assertEqual((cue.epoch_index, cue.epoch_tick), (epoch, offset))
                self.assertEqual(cue.tick, tick)
                self.assertEqual(cue.public()["schema"], EPOCH_SCHEMA)
        for values in (
            (EPOCH_TICKS, 0, 0),
            (EPOCH_TICKS, 1, 1),
            (0, True, 0),
            (0, 0, False),
            (-1, 0, 0),
            (MAX_ABSOLUTE_TICKS, 10, 9),
        ):
            with self.subTest(values=values), self.assertRaises(EpochSenseError):
                EpochProprioception(*values, "a" * 64, .16, .16, False)
        with self.assertRaises(EpochSenseError):
            EpochProprioception(0, 0, 0, "a" * 64, 10 ** 1000, .16, False)
        with self.assertRaises(ValueError):
            Proprioception(EPOCH_TICKS, "a" * 64, .16, .16, False)

    def test_v4_snapshot_rejects_schema_and_epoch_forgery(self):
        model = CellExplorerV4()
        cue = self.sense(0)
        model.observe(cue, model.decide(cue), False)
        good = model.snapshot()
        self.assertEqual(CellExplorerV4.from_snapshot(good).snapshot(), good)
        for field, replacement in (
            ("schema", "mojum.developmental.local-cell/3"),
            ("last_epoch_index", True),
            ("last_epoch_index", 1),
            ("updates", True),
        ):
            with self.subTest(field=field, replacement=replacement):
                bad = deepcopy(good)
                bad[field] = replacement
                with self.assertRaises(CellV4Error):
                    CellExplorerV4.from_snapshot(bad)

    def test_blocked_rest_is_rejected_before_policy_mutation(self):
        model = CellExplorerV4()
        cue = self.sense(0)
        intent = model.decide(cue)
        self.assertEqual(intent["action"], "rest")
        before = model.snapshot()
        with self.assertRaisesRegex(CellV4Error, "blocked rest"):
            model.observe(cue, intent, True)
        self.assertEqual(model.snapshot(), before)


class EpochJournalV4Tests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.db = Path(scratch.name) / "v4-private.sqlite3"

    def open(self):
        return Aquarium(
            self.db, SEED, GRAPH,
            motor_mode=V4_MODE,
            neural_mode="disabled",
        )

    def test_preflight_counts_epoch_marker_without_partial_write(self):
        with self.open() as aquarium:
            self.assertEqual(aquarium.required_events_for_steps(9_999), 9_999)
            self.assertEqual(aquarium.required_events_for_steps(10_000), 10_001)
            self.assertEqual(aquarium.required_events_for_steps(10_050), 10_051)
            before = aquarium.checkpoint()
            with patch("mojum.aquarium.MAX_REPLAY_EVENTS", aquarium.stored_events):
                with self.assertRaisesRegex(AquariumError, "remaining journal event budget"):
                    aquarium.step()
            self.assertEqual(aquarium.checkpoint(), before)
            self.assertEqual(aquarium.store.count(), 1)

    def test_wire_rejects_foreign_topic_with_valid_hash_chain(self):
        """Inspector must enforce the same event-topic set as v4 replay."""
        with self.open() as aquarium:
            foreign = aquarium._record(
                "development.advance", "development-000-001",
                {
                    "schema": "mojum.agent.development-event/1",
                    "from_months": 0, "to_months": 1,
                    "reason": "synthetic foreign-topic acceptance regression",
                },
                aquarium.tick * aquarium.dt_ns, aquarium.genome.revision,
            )
            aquarium.store.append(foreign, expected_head=aquarium.store.head())
            self.assertEqual(aquarium.store.verify_chain()["count"], 2)
        with self.assertRaisesRegex(NoosphereError, "unsupported local-cell v4 journal topic"):
            inspect_journal(self.db)
        with self.assertRaisesRegex(AquariumError, "contain only frames"):
            self.open()

    def test_wire_rejects_v4_legacy_sensory_bootstrap_alias(self):
        # The legacy /1 association cap is not a valid v4 continuation. The
        # separately versioned /2 selector is exercised by sensory QA.
        from mojum.aquarium_wire import _stored_policy

        with self.open() as aquarium:
            manifest = deepcopy(aquarium.manifest)
        for sensory in ("toy_probe",):
            with self.subTest(sensory=sensory):
                bogus = deepcopy(manifest)
                bogus["sensory_mode"] = sensory
                with self.assertRaisesRegex(NoosphereError, "none or toy_probe_v2"):
                    _stored_policy(bogus)

    def test_wire_pins_v4_absolute_tick_cap_in_bootstrap(self):
        from mojum.aquarium_wire import _stored_policy

        with self.open() as aquarium:
            manifest = deepcopy(aquarium.manifest)
        for mutation in ("missing", MAX_ABSOLUTE_TICKS + 1):
            with self.subTest(mutation=mutation):
                bogus = deepcopy(manifest)
                if mutation == "missing":
                    del bogus["max_absolute_ticks"]
                else:
                    bogus["max_absolute_ticks"] = mutation
                with self.assertRaisesRegex(NoosphereError, "ambiguous or unknown"):
                    _stored_policy(bogus)

    def test_v4_cli_omits_motor_arguments_and_commits_real_frames(self):
        process = subprocess.run(
            [sys.executable, "-m", "mojum.aquarium",
             "--db", str(self.db), "--motor-mode", V4_MODE,
             "--neural-mode", "disabled", "--ticks", "2"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        checkpoint = json.loads(process.stdout)
        self.assertEqual(checkpoint["tick"], 2)
        self.assertEqual(checkpoint["stored_events"], 3)
        with self.open() as resumed:
            self.assertEqual(resumed.tick, 2)
            self.assertEqual(resumed.checkpoint()["journal_head"], checkpoint["journal_head"])

    def test_wire_rejects_noncanonical_frame_envelope_with_valid_hash_chain(self):
        # Metadata-only inspection must not accept a frame whose event ID or
        # recorded logical time is inconsistent with the canonical v4 replay.
        for label, event_id, observed_at_ns in (
            ("wrong-id", "forged-frame-000", 0),
            ("wrong-time", "frame-000000000000", 1),
        ):
            with self.subTest(label=label):
                path = self.db.with_name(f"{label}.sqlite3")
                with Aquarium(
                    path, SEED, GRAPH, motor_mode=V4_MODE, neural_mode="disabled"
                ) as aquarium:
                    frame = deepcopy(aquarium._calculate_frame(None, None)[0])
                    forged = aquarium._record(
                        "aquarium.frame", event_id, frame,
                        observed_at_ns, aquarium.genome.revision,
                    )
                    aquarium.store.append(
                        forged, expected_head=aquarium.store.head(),
                    )
                    self.assertEqual(aquarium.store.verify_chain()["count"], 2)
                with self.assertRaisesRegex(NoosphereError, "noncanonical v4 frame envelope"):
                    inspect_journal(path)

    def test_wire_rejects_premature_epoch_marker_with_valid_hash_chain(self):
        """Read-only inspection must enforce the boundary, not just SHA-256."""
        with self.open() as aquarium:
            marker = aquarium._epoch_event(1)
            aquarium._append_record(
                marker["topic"], marker["event_id"], marker["payload"],
                marker["observed_at_ns"], marker["source_revision"],
            )
            # A well-formed, normally committed SQLite event may still be
            # semantically wrong: epoch 1 cannot begin before tick 9999.
            self.assertEqual(aquarium.store.verify_chain()["count"], 2)
            with self.assertRaisesRegex(NoosphereError, "epoch marker"):
                inspect_journal(self.db)

    @unittest.skipUnless(
        EPOCH_STRESS_ENABLED,
        "10k-frame epoch replay runs in the untraced full-suite step",
    )
    def test_real_sqlite_crosses_epoch_and_replays_after_durable_marker(self):
        with self.open() as aquarium:
            for _ in range(EPOCH_TICKS):
                aquarium.step()
            self.assertEqual((aquarium.tick, aquarium.epoch_index), (EPOCH_TICKS, 0))
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 1)
            before = aquarium.checkpoint()
            for args in ({"dx": 0.0}, {"dy": 0.0}, {"dx": "invalid"},
                         {"dx": None}, {"dy": None}, {"dx": None, "dy": None}):
                with self.subTest(args=args):
                    with self.assertRaisesRegex(
                        AquariumError, "rejects external dx/dy"
                    ):
                        aquarium.step(**args)
                    self.assertEqual(aquarium.checkpoint(), before)
                    self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 1)
            with patch.object(
                aquarium, "_calculate_frame", side_effect=RuntimeError("boundary frame")
            ):
                with self.assertRaisesRegex(RuntimeError, "boundary frame"):
                    aquarium.step()
            self.assertEqual((aquarium.tick, aquarium.epoch_index), (EPOCH_TICKS, 1))
            self.assertEqual(aquarium.store.count(), EPOCH_TICKS + 2)
            marker = next(
                row["event"] for row in aquarium.store.iter_events(include_nonpublic=True)
                if row["seq"] == EPOCH_TICKS + 2
            )
            self.assertEqual(marker["topic"], "aquarium.epoch")
            self.assertEqual(marker["event_id"], "epoch-00000001")
            self.assertEqual(marker["payload"]["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(marker["observed_at_ns"], EPOCH_TICKS * aquarium.dt_ns)
            after_marker = aquarium.checkpoint()
            self.assertNotEqual(before["journal_head"], after_marker["journal_head"])

        with self.open() as resumed:
            self.assertEqual(resumed.checkpoint(), after_marker)
            first = resumed.step()
            self.assertEqual(first["tick"], EPOCH_TICKS)
            observation = first["developmental"]["observation"]
            self.assertEqual(observation["schema"], EPOCH_SCHEMA)
            self.assertEqual(observation["absolute_tick"], EPOCH_TICKS)
            self.assertEqual(observation["epoch_index"], 1)
            self.assertEqual(observation["epoch_tick"], 0)
            for _ in range(50):
                resumed.step()
            expected = resumed.checkpoint()
            self.assertEqual((resumed.tick, resumed.epoch_index), (10_050, 1))
            self.assertEqual(resumed.store.count(), 10_052)
            self.assertEqual(expected["epoch_tick"], 51)

        with self.open() as cold:
            self.assertEqual(cold.checkpoint(), expected)
            self.assertEqual(cold.store.verify_chain()["head"], expected["journal_head"])
            wire = inspect_journal(self.db, external_expected_head=expected["journal_head"])
            self.assertEqual(wire["format"], V4_MODE)
            self.assertEqual(wire["frames"], 10_050)
            self.assertEqual(wire["events"], 10_052)
            self.assertEqual(wire["epoch_schema"], EPOCH_SCHEMA)
            self.assertEqual(wire["epoch_ticks"], EPOCH_TICKS)

    def test_wire_inspector_rejects_marker_free_boundary_frame(self):
        # Use a reduced inspector threshold against a real valid SQLite/hash
        # chain containing three consecutive v4 frames and no marker.
        with self.open() as aquarium:
            for _ in range(3):
                aquarium.step()
        with patch("mojum.aquarium_wire.EPOCH_TICKS", 2):
            with self.assertRaisesRegex(NoosphereError, "missing durable v4 epoch marker"):
                inspect_journal(self.db)

    def test_wire_inspector_rejects_early_epoch_marker(self):
        # An independent writer can append a syntactically valid event and
        # preserve the full event hash chain. The wire ABI must reject it.
        with self.open() as aquarium:
            aquarium.step()
            event = aquarium._record(
                "aquarium.epoch", "epoch-00000001",
                {
                    "schema": "mojum.aquarium.epoch-transition/1",
                    "absolute_tick": 1,
                    "epoch_index": 1,
                    "previous_epoch_index": 0,
                    "epoch_tick": 0,
                    "source": "explicit_bounded_epoch_rollover_without_reset",
                },
                aquarium.dt_ns, aquarium.genome.revision,
            )
            aquarium.store.append(
                event, expected_head=aquarium.checkpoint()["journal_head"]
            )
        with self.assertRaisesRegex(NoosphereError, "epoch"):
            inspect_journal(self.db)

    def test_wire_inspector_rejects_boolean_observation_ticks_with_valid_hash_chain(self):
        # A hash-valid event must not be reported as a v4 frame when bools
        # impersonate exact integer fields (False == 0 in Python).
        for field in ("absolute_tick", "epoch_index", "epoch_tick"):
            with self.subTest(field=field):
                path = self.db.with_name(f"bool-observation-{field}.sqlite3")
                with Aquarium(
                    path, SEED, GRAPH,
                    motor_mode=V4_MODE, neural_mode="disabled",
                ) as aquarium:
                    frame = deepcopy(aquarium._calculate_frame(None, None)[0])
                    frame["developmental"]["observation"][field] = False
                    forged = aquarium._record(
                        "aquarium.frame", "frame-000000000000",
                        frame, 0, aquarium.genome.revision,
                    )
                    aquarium.store.append(
                        forged, expected_head=aquarium.checkpoint()["journal_head"],
                    )
                    self.assertEqual(aquarium.store.verify_chain()["count"], 2)
                with self.assertRaisesRegex(NoosphereError, "epoch schema"):
                    inspect_journal(path)

    def test_wire_inspector_rejects_forged_v4_intent_and_outcome_clocks(self):
        # A valid event hash chain cannot make a forged policy clock canonical.
        # Test both JSON bool/int aliases and ordinary divergent integers.
        cases = (
            ("intent", "tick", False),
            ("intent", "epoch_index", False),
            ("intent", "epoch_tick", False),
            ("learning", "epoch_index", False),
            ("learning", "epoch_tick", False),
            ("intent", "tick", 1),
            ("learning", "epoch_tick", 1),
        )
        for index, (section, field, fake) in enumerate(cases):
            with self.subTest(section=section, field=field, fake=fake):
                path = self.db.with_name(f"bad-v4-clock-{index}.sqlite3")
                with Aquarium(
                    path, SEED, GRAPH,
                    motor_mode=V4_MODE, neural_mode="disabled",
                ) as aquarium:
                    frame = deepcopy(aquarium._calculate_frame(None, None)[0])
                    frame["developmental"][section][field] = fake
                    forged = aquarium._record(
                        "aquarium.frame", "frame-000000000000",
                        frame, 0, aquarium.genome.revision,
                    )
                    aquarium.store.append(
                        forged, expected_head=aquarium.checkpoint()["journal_head"],
                    )
                    self.assertEqual(aquarium.store.verify_chain()["count"], 2)
                with self.assertRaisesRegex(NoosphereError, "epoch schema"):
                    inspect_journal(path)

    def test_epoch_marker_rejects_bool_int_aliases_on_replay(self):
        # Python True == 1 and False == 0, but the durable schema
        # requires exact JSON integer types, never boolean aliases.
        with self.open() as aquarium:
            aquarium.tick = EPOCH_TICKS
            canonical = aquarium._epoch_event(1)
            for field, fake in (
                ("epoch_index", True),
                ("previous_epoch_index", False),
                ("epoch_tick", False),
            ):
                with self.subTest(field=field):
                    forged = deepcopy(canonical)
                    forged["payload"][field] = fake
                    with self.assertRaisesRegex(AquariumError, "invalid epoch"):
                        aquarium._replay_event({"event": forged})
                    self.assertEqual(aquarium.epoch_index, 0)

    def test_wire_inspector_rejects_bool_epoch_index_with_valid_hash_chain(self):
        with self.open() as aquarium:
            aquarium.step()
            forged = aquarium._epoch_event(1)
            forged["payload"]["epoch_index"] = True
            forged["payload"]["previous_epoch_index"] = False
            forged["payload"]["epoch_tick"] = False
            aquarium.store.append(
                forged, expected_head=aquarium.checkpoint()["journal_head"]
            )
        # Only the inspector's boundary period is shortened here. The
        # immutable bootstrap policy and the actual journal remain v4.
        # This isolates type checking from the real 9999-frame stress test.
        with patch("mojum.aquarium_wire.EPOCH_TICKS", 1):
            with self.assertRaisesRegex(NoosphereError, "epoch"):
                inspect_journal(self.db)

    def test_v4_journal_is_not_reinterpreted_as_v3(self):
        with self.open() as aquarium:
            aquarium.step()
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0],
                "delete",
            )
        before = self.db.read_bytes()
        with self.assertRaisesRegex(AquariumError, "mode mismatch"):
            with Aquarium(self.db, SEED, GRAPH, motor_mode=V3_MODE, neural_mode="disabled"):
                pass
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
