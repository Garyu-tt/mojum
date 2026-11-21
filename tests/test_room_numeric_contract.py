"""R0.15 opt-in exact int/float room geometry: original world revision stable.

All tests use new temporary synthetic SQLite journals; old default policy
bootstrap bytes never change. This is not a retrofit of old ambiguous rooms.
"""
from __future__ import annotations

from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from mojum.aquarium import Aquarium, AquariumError, _room_manifest
from mojum.room_contract import (
    SCHEMA, RoomContractError, encode_room_exact, decode_room_exact,
)
from mojum.world_room import Room, _revision, advance, initial_state

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


def stored_bootstrap(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT wire_json FROM events WHERE seq=1").fetchone()
    return json.loads(row[0])["payload"]


class ExactRoomScalarContractTests(unittest.TestCase):
    def test_mix_of_int_float_preserves_revision_beyond_legacy_projection(self):
        base = Room(4, 4.0, ((2, 1.0, 0.125, 2.0),))
        start = (1.0, 2.0)
        contract = encode_room_exact(base, start)
        self.assertEqual(contract["schema"], SCHEMA)
        self.assertEqual(contract["width"], {"type": "int", "value": 4})
        self.assertEqual(contract["height"], {"type": "float", "hex": "0x1.0000000000000p+2"})
        self.assertEqual(contract["obstacles"][0][2],
                         {"type": "float", "hex": "0x1.0000000000000p-3"})
        roundtrip = json.loads(json.dumps(contract, sort_keys=True))
        reconstructed, restored_start = decode_room_exact(
            roundtrip, legacy_room=_room_manifest(base, start))
        self.assertEqual(type(reconstructed.width), int)
        self.assertEqual(type(reconstructed.height), float)
        self.assertEqual([type(x) for x in reconstructed.obstacles[0]],
                         [int, float, float, float])
        self.assertEqual(restored_start, start)
        self.assertEqual(type(restored_start[0]), float)
        self.assertEqual(_revision(base, 0, *start),
                         _revision(reconstructed, 0, *restored_start))
        initial = initial_state(base, *start)
        restored = initial_state(reconstructed, *restored_start)
        for tick in range(1, 4):
            initial, observed = advance(base, initial, tick=tick,
                                        dx=0.04, dy=0.0,
                                        expected_revision=initial.revision)
            restored, restored_obs = advance(reconstructed, restored, tick=tick,
                                             dx=0.04, dy=0.0,
                                             expected_revision=restored.revision)
            self.assertEqual(initial.revision, restored.revision)
            self.assertEqual(observed, restored_obs)

    def test_original_float_and_int_width_share_legacy_bootstrap_not_revision(self):
        integer_room = Room(4, 4, ((2, 1, 0.1, 2),))
        float_room = Room(4.0, 4.0, ((2, 1, 0.1, 2),))
        start = (1.0, 2.0)
        self.assertEqual(_room_manifest(integer_room, start),
                         _room_manifest(float_room, start))
        self.assertNotEqual(_revision(integer_room, 0, *start),
                            _revision(float_room, 0, *start))
        self.assertNotEqual(encode_room_exact(integer_room, start),
                            encode_room_exact(float_room, start))

    def test_strict_rejection_of_ambiguous_numeric_contracts(self):
        room = Room(4, 4, ((2, 1, 0.1, 2),))
        value = encode_room_exact(room, (1.0, 2.0))
        mutants = []
        for entry in (
            {"type": "float", "hex": "4.0"},  # valid but not canonical
            {"type": "float", "hex": "nan"},
            {"type": "float", "hex": "inf"},
            {"type": "int", "value": 4.0},
            {"type": "int", "value": True},
            {"type": "int", "value": 2**64},
            {"type": "float", "hex": "0x1.0000000000000P+2"},
            {"type": "float", "hex": "0x1.0000000000000p+2", "extra": 1},
        ):
            if entry == value["width"]:
                continue
            bad = copy.deepcopy(value)
            bad["width"] = entry
            mutants.append(bad)
        for key in ("schema", "obstacles", "start"):
            bad = copy.deepcopy(value)
            bad.pop(key)
            mutants.append(bad)
        bad = copy.deepcopy(value)
        bad["start"] = [{"type": "int", "value": 1}, value["start"][1]]
        mutants.append(bad)
        bad = copy.deepcopy(value)
        bad["obstacles"] = [[{"type": "int", "value": 1}]]
        mutants.append(bad)
        bad = copy.deepcopy(value)
        bad["schema"] = "mojum.world.room-exact-scalars/999"
        mutants.append(bad)
        for bad in mutants:
            with self.subTest(bad=bad):
                with self.assertRaises(RoomContractError):
                    decode_room_exact(bad)
        with self.assertRaises(RoomContractError):
            decode_room_exact(value, legacy_room={"width": 99})
        # Python equality wrongly treats 4 and 4.0 as equivalent; serialized
        # world revision and bootstrap types must remain distinguishable.
        inconsistent = _room_manifest(room, (1.0, 2.0))
        inconsistent["width"] = 4
        with self.assertRaisesRegex(RoomContractError, "projection"):
            decode_room_exact(value, legacy_room=inconsistent)
        with self.assertRaises(RoomContractError):
            encode_room_exact(room, (True, 2.0))

    def test_encoder_never_generates_an_over_quota_contract(self):
        room = Room(2, 2, ((0.5, 0.5, 0.01, 0.01),) * 4097)
        with self.assertRaisesRegex(RoomContractError, "obstacle count"):
            encode_room_exact(room, (1.0, 1.0))

    def test_negative_zero_and_binary_float_hex_are_reversible(self):
        room = Room(2, 2.0, ())
        code = encode_room_exact(room, (-0.0, 0.1))
        decoded, position = decode_room_exact(code,
                                              legacy_room=_room_manifest(room, (-0.0, 0.1)))
        self.assertEqual(code["start"][0]["hex"], "-0x0.0p+0")
        self.assertEqual(position[0].hex(), "-0x0.0p+0")
        self.assertEqual(position[1].hex(), (0.1).hex())
        legacy_wrong_zero = _room_manifest(room, (-0.0, 0.1))
        legacy_wrong_zero["start"][0] = 0.0
        with self.assertRaisesRegex(RoomContractError, "projection"):
            decode_room_exact(code, legacy_room=legacy_wrong_zero)
        self.assertEqual(_revision(room, 0, -0.0, 0.1),
                         _revision(decoded, 0, *position))


class ExactRoomSQLiteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "synthetic.sqlite3"

    def open(self, room, mode, *, policy="local_cell_v2"):
        return Aquarium(
            self.db, SEED, GRAPH, room=room,
            exploration_mode="goal_free",
            developmental_policy=policy,
            room_numeric_contract=mode,
        )

    def test_new_typed_journal_cold_replays_exact_mixed_scalar_room(self):
        room = Room(4, 4.0, ((2, 1.0, 0.1, 2),))
        with self.open(room, "typed_v1") as writer:
            self.assertEqual(writer.manifest["room_exact"]["schema"], SCHEMA)
            self.assertEqual(writer.manifest["schema"], "mojum.aquarium.bootstrap/2")
            for _ in range(35):
                writer.step()
            expected = writer.checkpoint()
            first_head = writer.store.head()
            self.assertEqual(writer.store.verify_chain()["head"], first_head)
        manifest = stored_bootstrap(self.db)
        reconstructed, start = decode_room_exact(
            manifest["room_exact"], legacy_room=manifest["room"])
        self.assertEqual(start, (1.0, 2.0))
        self.assertEqual(type(reconstructed.width), int)
        self.assertEqual(type(reconstructed.height), float)
        self.assertEqual([type(x) for x in reconstructed.obstacles[0]],
                         [int, float, float, int])
        with self.open(reconstructed, "typed_v1") as reader:
            self.assertEqual(reader.checkpoint(), expected)
            self.assertEqual(reader.store.head(), first_head)
            reader.step()
            self.assertEqual(reader.tick, 36)
        with self.open(reconstructed, "typed_v1") as reopened:
            self.assertEqual(reopened.tick, 36)
            self.assertEqual(reopened.store.count(), 37)

    def test_zero_tick_bootstrap_detects_int_vs_float_and_no_append(self):
        room_int = Room(4, 4, ((2, 1, .1, 2),))
        room_float = Room(4.0, 4.0, ((2, 1, .1, 2),))
        with self.open(room_int, "typed_v1") as original:
            before = original.checkpoint()
            count = original.store.count()
        with self.assertRaisesRegex(AquariumError, "bootstrap|seed|room|clock"):
            with self.open(room_float, "typed_v1"):
                pass
        with self.assertRaisesRegex(AquariumError, "bootstrap|seed|room|clock"):
            with self.open(room_int, "legacy"):
                pass
        with self.open(room_int, "typed_v1") as reloaded:
            self.assertEqual(reloaded.checkpoint(), before)
            self.assertEqual(reloaded.store.count(), count)

    def test_legacy_policy_bootstrap_unchanged_by_optin_feature(self):
        room = Room(4, 4, ((2, 1, .1, 2),))
        with self.open(room, "legacy") as previous:
            previous.step()
            head = previous.store.head()
            before = previous.checkpoint()
            manifest = previous.manifest
            self.assertNotIn("room_exact", manifest)
            self.assertEqual(manifest["schema"], "mojum.aquarium.bootstrap/1")
        self.assertNotIn("room_exact", stored_bootstrap(self.db))
        with self.open(room, "legacy") as reread:
            self.assertEqual(reread.checkpoint(), before)
            self.assertEqual(reread.store.head(), head)
        with self.assertRaisesRegex(AquariumError, "bootstrap|seed|room|clock"):
            with self.open(room, "typed_v1"):
                pass
        with self.open(room, "legacy") as safe:
            self.assertEqual(safe.store.head(), head)

    def test_explicit_typed_mode_requires_new_archive_and_does_not_bootstrap(self):
        with self.assertRaisesRegex(AquariumError, "cannot be rewritten"):
            Aquarium(self.db, SEED, GRAPH, exploration_mode="goal_free",
                     developmental_policy="local_cell_v2_a_archive",
                     room_numeric_contract="typed_v1")
        self.assertFalse(self.db.exists())
        with self.assertRaisesRegex(AquariumError, "unknown room numeric"):
            Aquarium(self.db, SEED, GRAPH,
                     room_numeric_contract=True)
        self.assertFalse(self.db.exists())

    def test_cli_explicit_typed_journal_reopens_only_with_matching_flag(self):
        env = dict(__import__("os").environ, PYTHONPATH=str(ROOT / "src"))
        command = [
            sys.executable, "-m", "mojum.aquarium",
            "--db", str(self.db), "--ticks", "2",
            "--exploration-mode", "goal_free",
            "--developmental-policy", "local_cell_v2",
            "--room-numeric-contract", "typed_v1",
        ]
        run = subprocess.run(command, cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr[-1000:])
        original_head = json.loads(run.stdout)["journal_head"]
        self.assertIn("room_exact", stored_bootstrap(self.db))
        self.assertEqual(stored_bootstrap(self.db)["schema"],
                         "mojum.aquarium.bootstrap/2")
        absent_flag = subprocess.run(
            command[:7] + ["--ticks", "0", "--exploration-mode", "goal_free",
                           "--developmental-policy", "local_cell_v2"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(absent_flag.returncode, 0)
        with self.open(Room(4, 4, ((2, 1, .1, 2),)), "typed_v1") as q:
            self.assertEqual(q.store.head(), original_head)


if __name__ == "__main__":
    unittest.main()
