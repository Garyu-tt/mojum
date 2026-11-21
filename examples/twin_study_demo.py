"""Run: PYTHONPATH=src python examples/twin_study_demo.py

Synthetic observations only: no digital child is launched, trained or hosted.
"""
import json

from mojum.twin_study import (
    MEASUREMENT_SCHEMA, MEETING_SCHEMA, compare, create_study,
    record_measurement, record_meeting,
)

template = {
    "schema": "mojum.genesis.genotype/1",
    "genome_id": "unused-template-id",
    "revision": "fixture-r1",
    "inherited": {
        "heartbeat_bpm": 60,
        "body_scale_milli": 1000,
        "sensory_gain_milli": 1000,
    },
}
study = create_study(
    template, left_id="twin-lab-a", left_name="Aster",
    right_id="twin-lab-b", right_name="Birch",
)
for agent_id, eid, score, source in (
    ("twin-lab-a", "art-a-001", 420, "synthetic:mentor-a"),
    ("twin-lab-b", "art-b-001", 350, "synthetic:mentor-b"),
):
    study = record_measurement(
        study,
        {"schema": MEASUREMENT_SCHEMA, "agent_id": agent_id, "event_id": eid,
         "tick": 1, "domain": "art", "score_milli": score, "source": source},
        expected_revision=study["revision"],
    )
study = record_meeting(
    study,
    {"schema": MEETING_SCHEMA, "meeting_id": "playdate-001", "tick": 2,
     "activity": "shared-painting-exercise"},
    expected_revision=study["revision"],
)
print(json.dumps(compare(study, tick=2), indent=2, ensure_ascii=False, sort_keys=True))
