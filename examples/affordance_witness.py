"""Two synthetic numeric rooms, identical birth state, different learned action."""

import json
from mojum.goal_free_affordance import AffordanceMemory, SENSOR_SCHEMA
from mojum.world_room import Room, advance, initial_state


def run_case(room):
    state = initial_state(room, 1.0, 2.0)
    model = AffordanceMemory()
    events = []
    for tick in range(3):
        numeric = {
            "schema": SENSOR_SCHEMA, "tick": tick,
            "world_revision": state.revision, "visible": False,
            "reflectance_rgb": None, "range_bucket_10cm": None,
            "source": "synthetic_local_proximity_not_camera",
        }
        choice = model.choose(numeric, False)
        next_state, result = advance(
            room, state, tick=tick + 1,
            dx=choice["dx"], dy=choice["dy"],
            expected_revision=state.revision,
        )
        model.record(numeric, False, choice, result)
        events.append({"tick": tick, "action": choice["action"],
                       "blocked": result["blocked"]})
        state = next_state
    return {"actions": events, "evidence_updates": model.updates,
            "identity_verified": False,
            "claim": "synthetic_numeric_affordance_learning_only"}


def main():
    clear = run_case(Room(4, 4))
    blocked = run_case(Room(4, 4, ((1.01, 1.98, .05, .05),)))
    if not (clear["actions"][0]["action"] == blocked["actions"][0]["action"] == "x+"
            and clear["actions"][1]["action"] != blocked["actions"][1]["action"]):
        raise RuntimeError("fixed synthetic developmental comparison failed")
    print(json.dumps({"schema": "mojum.affordance-witness/1",
                      "clear_room": clear, "blocked_room": blocked},
                     sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
