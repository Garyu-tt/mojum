"""Manual 4,001-update stress check for the versioned sensory table.

This is a synthetic CPU-only integration check.  It demonstrates that a
contact-rich local fixture can cross the legacy 4,000-update boundary while
remaining replayable from the same private SQLite journal.  It does not prove
learning, object understanding, physical touch, or consciousness.
"""

import json
from pathlib import Path
import tempfile
import time

from mojum.aquarium import Aquarium
from mojum.sensory_associations_v2 import SCHEMA
from mojum.sensory_play import Toy, ToyScene
from mojum.world_room import Room


ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "examples/genesis_seed.json").read_text(encoding="utf-8"))
GRAPH = json.loads((ROOT / "examples/first_loop.json").read_text(encoding="utf-8"))
TOTAL_STEPS = 8_001


def main():
    started = time.perf_counter()
    room = Room(.32, .32)
    scene = ToyScene(
        width=.32,
        height=.32,
        toys=(Toy("spectator.synthetic", .16, .16, .15, (42, 90, 174), .4),),
    )

    with tempfile.TemporaryDirectory() as scratch:
        db = Path(scratch) / "sensory-v2-stress.sqlite3"
        with Aquarium(
            db,
            SEED,
            GRAPH,
            room=room,
            start=(.16, .16),
            neural_mode="disabled",
            motor_mode="developmental_fixture",
            sensory_mode="toy_probe_v2",
            sensory_scene=scene,
        ) as aquarium:
            for _ in range(TOTAL_STEPS):
                aquarium.step()
            checkpoint = aquarium.checkpoint()
            model_snapshot = aquarium.contact_model.snapshot()
            events = aquarium.store.count()

        with Aquarium(
            db,
            SEED,
            GRAPH,
            room=room,
            start=(.16, .16),
            neural_mode="disabled",
            motor_mode="developmental_fixture",
            sensory_mode="toy_probe_v2",
            sensory_scene=scene,
        ) as restored:
            assert restored.checkpoint() == checkpoint
            assert restored.contact_model.snapshot() == model_snapshot

    assert model_snapshot["schema"] == SCHEMA
    assert model_snapshot["updates"] == 4_001
    assert events == TOTAL_STEPS + 1
    print(
        json.dumps(
            {
                "steps": TOTAL_STEPS,
                "updates": model_snapshot["updates"],
                "retained_cues": len(model_snapshot["table"]),
                "forgotten_observations": model_snapshot["forgotten_observations"],
                "journal_events": events,
                "cold_replay": True,
                "seconds": round(time.perf_counter() - started, 2),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
