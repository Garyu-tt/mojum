"""Pinned lab scenery must survive caller access and real journal restarts."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from mojum.sensory_curiosity_lab import SensoryPlay, Toy, ToyScene
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parent.parent
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))


class SensorySceneImmutabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_scene_assignment_and_deletion_cannot_corrupt_committed_replay(self):
        changes = (
            ("replace_toys", lambda scene: setattr(scene, "toys", ())),
            ("replace_room", lambda scene: setattr(scene, "room", Room(5.0, 4.0))),
            ("delete_toys", lambda scene: delattr(scene, "toys")),
            ("delete_room", lambda scene: delattr(scene, "room")),
        )
        for label, change in changes:
            with self.subTest(change=label):
                path = self.directory / (label + ".sqlite3")
                control_path = self.directory / (label + "-control.sqlite3")
                scene = ToyScene()
                manifest = scene.manifest()
                with SensoryPlay(control_path, GRAPH) as control:
                    with SensoryPlay(path, GRAPH, scene=scene) as live:
                        for _ in range(12):
                            self.assertEqual(live.step(), control.step())
                        before = live.checkpoint()
                        head, count = live.store.head(), live.store.count()

                        # A normal caller retains the scene passed at startup.
                        # Its edits must not silently change future history
                        # under the original durable bootstrap manifest.
                        with self.assertRaises(AttributeError):
                            change(scene)

                        self.assertEqual(scene.manifest(), manifest)
                        self.assertEqual(live.checkpoint(), before)
                        self.assertEqual(live.store.head(), head)
                        self.assertEqual(live.store.count(), count)
                        for _ in range(6):
                            self.assertEqual(live.step(), control.step())
                        committed = live.checkpoint()
                        self.assertEqual(committed, control.checkpoint())
                        self.assertGreater(live.model.updates, 0)

                    # Cold replay uses a newly constructed original scene,
                    # not the caller's retained object or a memory snapshot.
                    with SensoryPlay(path, GRAPH) as reopened:
                        self.assertEqual(reopened.checkpoint(), committed)
                        self.assertEqual(reopened.store.count(), count + 6)
                        self.assertEqual(reopened.step(), control.step())
                        self.assertEqual(reopened.checkpoint(), control.checkpoint())

    def test_nested_room_and_toy_values_remain_immutable(self):
        scene = ToyScene()
        before = scene.manifest()
        changes = (
            (scene.room, "width", 5.0),
            (scene.room, "obstacles", ((0.0, 0.0, 0.5, 0.5),)),
            (scene.toys[0], "x", 1.5),
            (scene.toys[0], "softness", 0.1),
            (scene.toys[0], "rgb", (0, 0, 0)),
        )
        for obj, attribute, replacement in changes:
            for operation in ("assign", "delete"):
                with self.subTest(attribute=attribute, operation=operation):
                    with self.assertRaises(AttributeError):
                        if operation == "assign":
                            setattr(obj, attribute, replacement)
                        else:
                            delattr(obj, attribute)
                    self.assertEqual(scene.manifest(), before)
        with self.assertRaises(TypeError):
            scene.toys[0].rgb[0] = 0
        with self.assertRaises(TypeError):
            scene.toys[0] = scene.toys[1]
        self.assertEqual(scene.manifest(), before)

    def test_mutating_exported_scene_views_preserves_live_and_cold_history(self):
        scene = ToyScene()
        original = scene.manifest()
        view = scene.manifest()
        view["width"] = 30.0
        view["toys"][0]["rgb"][0] = 0
        view["toys"][0]["softness"] = 0.0
        view["toys"].pop()
        self.assertEqual(scene.manifest(), original)

        path = self.directory / "views.sqlite3"
        with SensoryPlay(self.directory / "control.sqlite3", GRAPH) as control:
            with SensoryPlay(path, GRAPH, scene=scene) as live:
                expected = control.step()
                exported = live.step()
                self.assertEqual(exported, expected)
                before = live.checkpoint()
                exported["spectator_scene"]["toys"][0]["rgb"][0] = 0
                exported["spectator_scene"]["toys"].clear()
                self.assertEqual(scene.manifest(), original)
                self.assertEqual(live.checkpoint(), before)
                rows = live.store.dataset(include_nonpublic=True, limit=2)["projection"]["items"]
                self.assertEqual(rows[-1]["event"]["payload"], expected)
                self.assertEqual(live.step(), control.step())
                committed = live.checkpoint()
            with SensoryPlay(path, GRAPH, scene=ToyScene()) as reopened:
                self.assertEqual(reopened.checkpoint(), committed)
                self.assertEqual(reopened.step(), control.step())

    def test_custom_and_empty_scenes_resume_from_existing_bootstrap(self):
        factories = (
            ("default", ToyScene),
            ("custom", lambda: ToyScene(
                width=6.0, height=5.0,
                toys=(Toy("custom", 1.2, 2.0, 0.15, (11, 22, 33), 0.25),),
            )),
            ("empty", lambda: ToyScene(width=5.0, height=4.0, toys=())),
        )
        for label, make_scene in factories:
            with self.subTest(scene=label):
                path = self.directory / (label + ".sqlite3")
                with SensoryPlay(path, GRAPH, scene=make_scene()) as original:
                    bootstrap_state = original.checkpoint()
                    bootstrap_manifest = copy.deepcopy(original.manifest)
                    self.assertEqual(original.store.count(), 1)

                with SensoryPlay(self.directory / (label + "-control.sqlite3"),
                                GRAPH, scene=make_scene()) as control:
                    with SensoryPlay(path, GRAPH, scene=make_scene()) as resumed:
                        self.assertEqual(resumed.checkpoint(), bootstrap_state)
                        self.assertEqual(resumed.manifest, bootstrap_manifest)
                        for _ in range(24):
                            self.assertEqual(resumed.step(), control.step())
                        committed = resumed.checkpoint()
                        self.assertEqual(committed, control.checkpoint())
                        self.assertEqual(resumed.store.count(), 25)
                        if label == "empty":
                            self.assertEqual(resumed.model.updates, 0)
                        else:
                            self.assertGreater(resumed.model.updates, 0)
                    with SensoryPlay(path, GRAPH, scene=make_scene()) as reopened:
                        self.assertEqual(reopened.checkpoint(), committed)
                        self.assertEqual(reopened.step(), control.step())


if __name__ == "__main__":
    unittest.main()
