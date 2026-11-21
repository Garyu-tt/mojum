"""Read-only layout identification and rejection of wrong writable opens."""

from contextlib import closing
from importlib.resources import files
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError
from mojum.aquarium_wire import inspect_journal
from mojum.noosphere import LocalNoosphere, NoosphereError
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import Room


DATA = files("mojum").joinpath("data")
SEED = json.loads(DATA.joinpath("genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads(DATA.joinpath("first_loop.json").read_text(encoding="utf-8"))
SENSORY_ROOM = Room(0.32, 0.32)
SENSORY_SCENE = ToyScene(
    width=0.32,
    height=0.32,
    toys=(Toy("spectator.synthetic", 0.16, 0.16, 0.15, (42, 90, 174), 0.4),),
)


class AquariumWireTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.db = Path(scratch.name) / "synthetic.sqlite3"

    def aquarium(self, motor_mode="scripted"):
        return Aquarium(self.db, SEED, GRAPH, motor_mode=motor_mode)

    def sensory_aquarium(self):
        return Aquarium(
            self.db,
            SEED,
            GRAPH,
            room=SENSORY_ROOM,
            start=(0.16, 0.16),
            neural_mode="disabled",
            motor_mode="developmental_fixture",
            sensory_mode="toy_probe_v2",
            sensory_scene=SENSORY_SCENE,
        )

    def committed_sensory_events(self, ticks=3):
        with self.sensory_aquarium() as aquarium:
            for _ in range(ticks):
                aquarium.step()
            return [
                item["event"]
                for item in aquarium.store.dataset(
                    include_nonpublic=True)["projection"]["items"]
            ]

    def committed_v4_sensory_events(self, ticks=3):
        with Aquarium(
            self.db.with_name("v4-sensory-source.sqlite3"),
            SEED,
            GRAPH,
            neural_mode="disabled",
            motor_mode="local_cell_v4",
            sensory_mode="toy_probe_v2",
        ) as aquarium:
            for _ in range(ticks):
                aquarium.step()
            return [
                item["event"]
                for item in aquarium.store.dataset(
                    include_nonpublic=True)["projection"]["items"]
            ]

    def rewrite_journal(self, events, name):
        """Re-append events into a fresh chain so only the payload is forged."""
        path = self.db.with_name(name)
        with LocalNoosphere(path, events[0]["agent_id"]) as store:
            head = store.head()
            for event in events:
                head = store.append(event, expected_head=head)
        return path, head

    def assert_forged_frame_rejected(self, mutate, pattern, name):
        events = json.loads(json.dumps(self.committed_sensory_events()))
        frames = [event for event in events if event["topic"] == "aquarium.frame"]
        self.assertGreaterEqual(len(frames), 3)
        mutate(frames)
        path, head = self.rewrite_journal(events, name)
        with self.assertRaisesRegex(NoosphereError, pattern):
            inspect_journal(path, external_expected_head=head)

    def test_inspector_rejects_forged_sensory_frame_ticks(self):
        def observation_tick(frames):
            frames[0]["payload"]["toy_contact"]["observation"]["tick"] = 99

        def probe_tick(frames):
            frames[0]["payload"]["toy_contact"]["probe_intent"]["tick"] = 99

        def measurement_tick(frames):
            contact = frames[0]["payload"]["toy_contact"]
            self.assertIsNotNone(contact["measured_contact"])
            contact["measured_contact"]["source_tick"] = 99

        def stale_prediction(frames):
            frames[2]["payload"]["toy_contact"]["prediction_before"]["model_updates"] = 0

        cases = (
            (observation_tick, "observation disagrees with the frame tick",
             "forged-observation.sqlite3"),
            (probe_tick, "probe intent disagrees with the frame",
             "forged-probe.sqlite3"),
            (measurement_tick, "measurement disagrees with the frame tick",
             "forged-measurement.sqlite3"),
            (stale_prediction, "sensory prediction disagrees with the frame",
             "forged-prediction.sqlite3"),
        )
        for mutate, pattern, name in cases:
            with self.subTest(case=name):
                self.assert_forged_frame_rejected(mutate, pattern, name)

    def test_inspector_rejects_inconsistent_measurement_and_learning_presence(self):
        def drop_learning(frames):
            contact = frames[0]["payload"]["toy_contact"]
            self.assertIsNotNone(contact["learning"])
            contact["learning"] = None

        def unrequested_measurement(frames):
            contact = frames[0]["payload"]["toy_contact"]
            self.assertIsNotNone(contact["measured_contact"])
            contact["probe_intent"]["requested"] = False
            contact["probe_intent"]["reason"] = "no_probe_needed"

        self.assert_forged_frame_rejected(
            drop_learning,
            "measurement and learning evidence disagree",
            "forged-learning-drop.sqlite3",
        )
        self.assert_forged_frame_rejected(
            unrequested_measurement,
            "probe request and measurement disagree",
            "forged-learning-extra.sqlite3",
        )

    def test_inspector_replays_the_complete_sensory_state_transition(self):
        def state_digest(frames):
            contact = frames[0]["payload"]["toy_contact"]
            contact["association_state_sha256"] = "0" * 64

        def prediction_cue(frames):
            contact = frames[0]["payload"]["toy_contact"]
            contact["prediction_before"]["cue"] = "0:0:0"

        def learning_status(frames):
            contact = frames[0]["payload"]["toy_contact"]
            self.assertIsNotNone(contact["learning"])
            contact["learning"]["status"] = "forged_status"

        def impossible_deflection(frames):
            contact = frames[0]["payload"]["toy_contact"]
            self.assertIsNotNone(contact["measured_contact"])
            contact["measured_contact"]["measured_deflection"] = 2.0

        cases = (
            (state_digest, "association state digest disagrees with replay",
             "forged-association-state.sqlite3"),
            (prediction_cue, "prediction disagrees with the frame",
             "forged-prediction-cue.sqlite3"),
            (learning_status, "learning evidence is not replayable",
             "forged-learning-status.sqlite3"),
            (impossible_deflection, "measurement is not replayable",
             "forged-measured-deflection.sqlite3"),
        )
        for mutate, pattern, name in cases:
            with self.subTest(case=name):
                self.assert_forged_frame_rejected(mutate, pattern, name)

    def test_inspector_rejects_sensory_revision_detached_from_world(self):
        cases = (
            (self.committed_sensory_events, 1,
             "forged-sensory-world-continuity.sqlite3"),
            (self.committed_v4_sensory_events, 0,
             "forged-v4-sensory-motor-revision.sqlite3"),
        )
        for make_events, frame_index, name in cases:
            with self.subTest(case=name):
                events = json.loads(json.dumps(make_events()))
                frames = [
                    event for event in events
                    if event["topic"] == "aquarium.frame"
                ]
                contact = frames[frame_index]["payload"]["toy_contact"]
                contact["observation"]["world_revision"] = "0" * 64
                contact["probe_intent"]["source_revision"] = "0" * 64
                path, head = self.rewrite_journal(events, name)
                with self.assertRaisesRegex(
                        NoosphereError, "causal world revision"):
                    inspect_journal(path, external_expected_head=head)

    def test_genuine_versioned_sensory_journal_still_inspects(self):
        events = self.committed_sensory_events()
        path, head = self.rewrite_journal(events, "genuine-sensory.sqlite3")
        info = inspect_journal(path, external_expected_head=head)
        self.assertEqual(info["format"], "motor_play_v1+toy_probe_v2")
        self.assertEqual(info["frames"], 3)

    def make_rollback_journal(self):
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        if os.name == "posix":
            self.db.chmod(0o640)
        return self.db.read_bytes()

    def test_wrong_mode_does_not_change_sqlite_file_or_permissions(self):
        with self.aquarium() as aquarium:
            aquarium.step()
            saved = aquarium.checkpoint()
        wire = self.make_rollback_journal()
        mode = self.db.stat().st_mode
        with self.assertRaisesRegex(AquariumError, "mode mismatch"):
            self.aquarium("developmental_fixture")
        self.assertEqual(self.db.read_bytes(), wire)
        self.assertEqual(self.db.stat().st_mode, mode)
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with self.aquarium() as restored:
            self.assertEqual(restored.checkpoint(), saved)
            self.assertEqual(restored.step()["tick"], 1)

    def test_existing_events_without_metadata_fail_before_writable_repair(self):
        for mutation in ("delete_rows", "drop_table"):
            with self.subTest(mutation=mutation):
                path = self.db if mutation == "delete_rows" else self.db.with_name(
                    "missing-metadata-table.sqlite3")
                with Aquarium(path, SEED, GRAPH) as aquarium:
                    aquarium.step(dx=0.01, dy=0.0)
                with closing(sqlite3.connect(path)) as connection:
                    if mutation == "delete_rows":
                        connection.execute("DELETE FROM metadata")
                    else:
                        connection.execute("DROP TABLE metadata")
                    connection.commit()
                before = path.read_bytes()
                with self.assertRaisesRegex(NoosphereError, "metadata is missing"):
                    with Aquarium(path, SEED, GRAPH):
                        pass
                self.assertEqual(path.read_bytes(), before)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM events").fetchone()[0], 2)
                    if mutation == "delete_rows":
                        self.assertEqual(connection.execute("SELECT count(*) FROM metadata").fetchone()[0], 0)
                    else:
                        self.assertIsNone(connection.execute(
                            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='metadata'"
                        ).fetchone())

    def test_inspector_reports_mode_and_anchor_with_writer_wal_open(self):
        with self.aquarium("developmental_fixture") as aquarium:
            aquarium.step()
            aquarium.advance_development()
            aquarium.step()
            checkpoint = aquarium.checkpoint()
            wal = self.db.with_suffix(".sqlite3-wal")
            journal_before = self.db.read_bytes()
            wal_before = wal.read_bytes()
            actual = inspect_journal(self.db, external_expected_head=checkpoint["journal_head"])
            self.assertEqual(actual, {
                "schema": "mojum.aquarium.wire-inspection/1",
                "format": "motor_play_v1",
                "state_schema": "mojum.developmental.motor-play/1",
                "temporal_schema": None,
                "frames": 2,
                "events": 4,
                "journal_head": checkpoint["journal_head"],
                "integrity": "chain-and-independent-head-checked",
            })
            with self.assertRaisesRegex(NoosphereError, "independent external anchor"):
                inspect_journal(self.db, external_expected_head="1" * 64)
            self.assertEqual(self.db.read_bytes(), journal_before)
            self.assertEqual(wal.read_bytes(), wal_before)
            self.assertEqual(aquarium.checkpoint(), checkpoint)

    def test_inspector_refuses_mixed_policy_bootstrap_even_with_valid_hash(self):
        with self.aquarium() as aquarium:
            event = aquarium.store.dataset(include_nonpublic=True)["projection"]["items"][0]["event"]
        foreign = self.db.with_name("mixed.sqlite3")
        event["payload"].update({
            "exploration_mode": "goal_free",
            "motor_mode": "developmental_fixture",
            "motor_policy_schema": "mojum.developmental.motor-play/1",
        })
        owner = event["agent_id"]
        with LocalNoosphere(foreign, owner) as store:
            head = store.append(event, expected_head=store.head())
            self.assertEqual(store.verify_chain()["head"], head)
        before = foreign.read_bytes()
        with self.assertRaisesRegex(NoosphereError, "ambiguous or unknown"):
            inspect_journal(foreign, external_expected_head=head)
        self.assertEqual(foreign.read_bytes(), before)

    def test_inspector_refuses_unpinned_contact_with_valid_hash_chain(self):
        # The independent inspector must not certify sensory observations
        # that the committed bootstrap never selected, even when the chain
        # has been recomputed correctly. Only a new synthetic copy is used.
        with self.aquarium() as aquarium:
            aquarium.step()
            events = [
                item["event"] for item in aquarium.store.dataset(
                    include_nonpublic=True
                )["projection"]["items"]
            ]
        self.assertEqual(len(events), 2)
        self.assertEqual(inspect_journal(self.db)["frames"], 1)
        bootstrap, frame = json.loads(json.dumps(events))
        self.assertNotIn("sensory_mode", bootstrap["payload"])
        frame["payload"]["toy_contact"] = {
            "schema": "mojum.sensory.toy-contact/2",
        }
        foreign = self.db.with_name("unselected-contact.sqlite3")
        with LocalNoosphere(foreign, bootstrap["agent_id"]) as store:
            head = store.head()
            for event in (bootstrap, frame):
                head = store.append(event, expected_head=head)
            self.assertEqual(store.verify_chain()["head"], head)
        before = foreign.read_bytes()
        with self.assertRaisesRegex(
            NoosphereError, "unexpected sensory frame"
        ):
            inspect_journal(foreign, external_expected_head=head)
        self.assertEqual(foreign.read_bytes(), before)

    def test_missing_file_and_empty_store(self):
        with self.assertRaises(NoosphereError):
            inspect_journal(self.db)
        self.assertFalse(self.db.exists())
        self.db.touch()
        with self.aquarium() as stocked:
            self.assertEqual(stocked.tick, 0)
        self.assertEqual(inspect_journal(self.db)["format"], "scripted_v1")


if __name__ == "__main__":
    unittest.main()
