import hashlib
import json
import unittest

from mojum.dataset_view import project_episodes
from mojum.public_index_staging import stage_public_index


def episode(eid, visibility, obs):
    return {"id": eid, "source": "toy", "visibility": visibility,
            "tick": 1 if eid == "private" else 2, "observation": obs}


class PublicIndexStagingTests(unittest.TestCase):
    def dataset(self):
        return project_episodes(
            [episode("private", "PRIVATE", "DO_NOT_INDEX"),
             episode("public", "PUBLIC", "hello-world")],
            allowed_sources={"toy"},
        )

    def test_only_canonical_public_records_are_staged(self):
        staged = stage_public_index(self.dataset())
        self.assertEqual(staged["schema"], "mojum.public-index-staging/1")
        self.assertEqual(len(staged["entries"]), 1)
        self.assertEqual(staged["entries"][0]["observation"], "hello-world")
        self.assertNotIn("DO_NOT_INDEX", repr(staged))
        self.assertEqual(staged["source_integrity"], "checksum-only-not-authenticated")

    def test_stable_identity(self):
        self.assertEqual(stage_public_index(self.dataset()),
                         stage_public_index(self.dataset()))

    def test_rejects_checksum_tampering(self):
        data = self.dataset()
        data["records"][0]["observation"] = "changed"
        with self.assertRaises(ValueError):
            stage_public_index(data)

    def test_rejects_visibility_injection(self):
        data = self.dataset()
        data["records"][0]["visibility"] = "PRIVATE"
        with self.assertRaises(ValueError):
            stage_public_index(data)

    def test_spoofed_numeric_schema_with_matching_checksum_is_rejected(self):
        # Equality alone is insufficient: a subclassed int can compare equal
        # to the wire name but will be serialized as a JSON number.
        class PretendInt(int):
            def __eq__(self, other):
                return other == "mojum.dataset-view/1"

        data = self.dataset()
        data["schema"] = PretendInt(1)
        body = json.dumps(
            {"schema": data["schema"], "records": data["records"]},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        data["sha256"] = hashlib.sha256(body).hexdigest()
        with self.assertRaises(ValueError):
            stage_public_index(data)

    def test_rejects_unknown_version(self):
        data = self.dataset()
        data["schema"] = "mojum.dataset-view/2"
        with self.assertRaises(ValueError):
            stage_public_index(data)

    def test_rejects_invalid_budgets(self):
        for bad in (True, -1, 1025):
            with self.assertRaises(ValueError):
                stage_public_index(self.dataset(), max_records=bad)
        with self.assertRaises(ValueError):
            stage_public_index(self.dataset(), max_records=0)

    def test_rejects_unsorted_or_duplicate_records(self):
        data = project_episodes([
            episode("p1", "PUBLIC", "x"),
            episode("p2", "PUBLIC", "y")],
            allowed_sources={"toy"})
        data["records"] = list(reversed(data["records"]))
        with self.assertRaises(ValueError):
            stage_public_index(data)

    def test_does_not_load_untrusted_blob(self):
        with self.assertRaises(ValueError):
            stage_public_index({"schema": "mojum.dataset-view/1", "records": [],
                                "sha256": "X" * 64})


if __name__ == "__main__":
    unittest.main()
