"""Cross-stack integration QA: versioned local-cell + streaming + HMAC.

All journal/key files are temporary, synthetic and PRIVATE; no real
Noosphere histories, production credentials, host or Android device.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mojum.aquarium import Aquarium, AquariumError, MAX_REPLAY_EVENTS
from mojum.cell_policy_v2 import SCHEMA as V2_SCHEMA
from mojum.cell_policy_v3 import SCHEMA as V3_SCHEMA
from mojum.developmental_policy import SCHEMA as LEGACY_SCHEMA
from mojum.verified_snapshot import (
    SnapshotError, create_key_file, load_snapshot, save_snapshot,
)
from mojum.world_room import Room

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SignedVersionedLocalCellQA(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.db = self.root / "synthetic-private.sqlite3"
        self.cache = self.root / "owner-replay.json"
        self.key = self.root / "owner-hmac.key"
        create_key_file(self.key)

    def aquarium(self, policy, *, signed=True):
        kwargs = {}
        if signed:
            kwargs.update(
                replay_snapshot_file=self.cache,
                replay_snapshot_key_file=self.key,
            )
        return Aquarium(
            self.db, SEED, GRAPH, room=Room(.16, .16),
            start=(.16, .16), exploration_mode="goal_free",
            developmental_policy=policy,
            **kwargs,
        )

    def test_signed_v3_skips_prefix_but_checks_streaming_tail(self):
        self.assertEqual(MAX_REPLAY_EVENTS, 100_000)
        with self.aquarium("local_cell_v3") as q:
            for _ in range(80):
                q.step()
            saved = q.checkpoint()
            q.save_replay_snapshot()
            self.assertEqual(
                load_snapshot(self.cache, self.key)["explorer"]["schema"],
                V3_SCHEMA,
            )
            self.assertEqual(q.store.count(), 81)
        # Advance the SAME real SQLite history without consuming signed cache
        # and without changing its old HMAC snapshot.
        with self.aquarium("local_cell_v3", signed=False) as unsnapped:
            self.assertEqual(unsnapped.checkpoint(), saved)
            for _ in range(25):
                unsnapped.step()
            target = unsnapped.checkpoint()
        observed = []
        original_calculate = Aquarium._calculate

        def counted(self, dx, dy):
            observed.append(self.tick)
            return original_calculate(self, dx, dy)

        with patch.object(Aquarium, "_calculate", counted):
            with self.aquarium("local_cell_v3") as fast:
                self.assertEqual(fast.checkpoint(), target)
                self.assertEqual(fast.explorer.snapshot()["schema"], V3_SCHEMA)
                self.assertEqual(fast.store.count(), 106)
                self.assertEqual(fast._committed_event_count, 106)
                self.assertEqual(fast.store.verify_chain()["head"],
                                 target["journal_head"])
                self.assertEqual(observed, list(range(80, 105)))
                # Persist new signed prefix and prove it restores with no
                # repeated simulation when the source head is identical.
                fast.save_replay_snapshot()
        observed.clear()
        with patch.object(Aquarium, "_calculate", counted):
            with self.aquarium("local_cell_v3") as restored:
                self.assertEqual(restored.checkpoint(), target)
                self.assertEqual(observed, [])
                restored.step()
                resumed = restored.checkpoint()
        with self.aquarium("local_cell_v3", signed=False) as full:
            self.assertEqual(full.checkpoint(), resumed)

    def test_policy_pinning_legacy_v2_v3_and_hmac_mismatch(self):
        schemas = {"legacy": LEGACY_SCHEMA,
                   "local_cell_v2": V2_SCHEMA,
                   "local_cell_v3": V3_SCHEMA}
        for selected in schemas:
            with self.subTest(selected=selected):
                # Three independent owner-only source DBs. Do not migrate
                # journal history across incompatible policy versions.
                location = self.root / (selected + ".sqlite3")
                sidecar = self.root / (selected + ".hmac.json")
                with Aquarium(
                    location, SEED, GRAPH, room=Room(.16, .16),
                    start=(.16, .16), exploration_mode="goal_free",
                    developmental_policy=selected,
                    replay_snapshot_file=sidecar,
                    replay_snapshot_key_file=self.key,
                ) as q:
                    for _ in range(15):
                        q.step()
                    old = q.checkpoint()
                    q.save_replay_snapshot()
                    self.assertEqual(old["developmental_state"]["schema"],
                                     schemas[selected])
                    if selected == "legacy":
                        self.assertNotIn("developmental_policy", q.manifest)
                    else:
                        self.assertEqual(q.manifest["developmental_policy"],
                                         selected)
                with Aquarium(
                    location, SEED, GRAPH, room=Room(.16, .16),
                    start=(.16, .16), exploration_mode="goal_free",
                    developmental_policy=selected,
                    replay_snapshot_file=sidecar,
                    replay_snapshot_key_file=self.key,
                ) as q:
                    self.assertEqual(q.checkpoint(), old)
                for wrong in set(schemas) - {selected}:
                    with self.subTest(selected=selected, wrong=wrong):
                        with self.assertRaises((AquariumError, SnapshotError)):
                            with Aquarium(
                                location, SEED, GRAPH, room=Room(.16, .16),
                                start=(.16, .16), exploration_mode="goal_free",
                                developmental_policy=wrong,
                                replay_snapshot_file=sidecar,
                                replay_snapshot_key_file=self.key,
                            ):
                                pass

    def test_valid_hmac_cannot_hide_wrong_explorer_or_rollback(self):
        with self.aquarium("local_cell_v3") as q:
            for _ in range(35):
                q.step()
            q.save_replay_snapshot()
            pinned = q.checkpoint()
        state = load_snapshot(self.cache, self.key)
        self.assertEqual(state["checkpoint"], pinned)
        # A holder of the HMAC key is allowed to re-sign data, but an
        # impossible signed state still must fail model/state validation.
        bad = copy.deepcopy(state)
        bad["explorer"]["schema"] = V2_SCHEMA
        save_snapshot(self.cache, self.key, bad)
        with self.assertRaises((SnapshotError, ValueError)):
            with self.aquarium("local_cell_v3"):
                pass
        # Signed previous snapshot restored, no original SQLite record
        # should be touched or recreated by any failed invocation.
        self.assertEqual(
            list(self.root.glob("*.sqlite3")), [self.db],
        )
        with self.aquarium("local_cell_v3", signed=False) as q:
            self.assertEqual(q.checkpoint(), pinned)
            self.assertEqual(q.store.count(), 36)


if __name__ == "__main__":
    unittest.main()
