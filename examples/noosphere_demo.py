"""Offline standalone Noosphere R0.1 smoke demo using a temporary SQLite file."""
import json
import tempfile
from pathlib import Path

from mojum.noosphere import LocalNoosphere, EVENT_SCHEMA


def fixture(i):
    return {
        "schema": EVENT_SCHEMA,
        "event_id": f"fixture-{i:03d}",
        "agent_id": "dante-fixture",
        "topic": "interoception.synthetic",
        "observed_at_ns": i * 100_000_000,
        "recorded_at_ns": i * 100_000_000,
        "valid_from_ns": i * 100_000_000,
        "valid_to_ns": None,
        "visibility": "PUBLIC",
        "source": "mojum.fixture.heartbeat",
        "source_revision": "fixture/1",
        "payload": {"pulse": i % 10 == 0, "tick": i, "classification": "synthetic-not-physiology"},
    }


def main():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "dante-noosphere.sqlite"
        with LocalNoosphere(db, "dante-fixture") as store:
            for i in range(12):
                store.append(fixture(i), expected_head=store.head())
            anchor = store.head()
            check = store.verify_chain(external_expected_head=anchor)
            dataset = store.dataset(topic="interoception.synthetic", limit=12)
        with LocalNoosphere(db, "dante-fixture") as reopened:
            assert reopened.count() == 12
            assert reopened.head() == anchor
            assert reopened.verify_chain(external_expected_head=anchor)["count"] == 12
        print(json.dumps({
            "status": "MOJUM_NOOSPHERE_R01_SYNTHETIC_FIXTURE",
            "persistent_reopen": True,
            "record_count": check["count"],
            "head_sha256": anchor,
            "dataset_sha256": dataset["sha256"],
            "offline": True,
            "neural_model_connected": False,
            "encrypted": False,
            "replicated": False,
        }, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
