"""Fail-closed Unicode input contract for synthetic somatic/identity/Bassy adapters."""

import unittest

from mojum import body_genetics as body


def chromosome():
    return {
        "schema": body.CHROMOSOME_SCHEMA,
        "genome_id": "genome-01",
        "inherited": {gene: 1000 for gene in body.SOMATIC_GENES},
    }


def manifest():
    return {
        "schema": body.ASSET_SCHEMA,
        "genome_id": "genome-01",
        "generator": "Bassy",
        "credits": {
            "text": "Made with Bassy",
            "url": "https://bassycharactercreator.itch.io/bassy-character-creator",
        },
        "stages": [
            {
                "age_years": 6,
                "path": "character/age_06.glb",
                "sha256": "a" * 64,
                "native_height_mm": 1150,
                "rig_id": "neutral",
            },
        ],
    }


class BodyUTF8AcceptanceTests(unittest.TestCase):
    def test_somatic_id_rejects_surrogate_as_contract_error(self):
        invalid = chromosome()
        invalid["genome_id"] = "genome-\ud800"
        with self.assertRaisesRegex(body.BodyContractError, "UTF-8"):
            body.validate_chromosome(invalid)

    def test_name_and_actor_id_reject_surrogates(self):
        for key in ("agent_id", "display_name"):
            with self.subTest(key=key):
                identity = {
                    "schema": body.IDENTITY_SCHEMA,
                    "agent_id": "genome-01",
                    "display_name": "Данте 🐈",
                    "assigned_by": "human",
                }
                identity[key] = "bad-\udfff"
                with self.assertRaisesRegex(body.BodyContractError, "UTF-8"):
                    body.validate_human_name(identity)

    def test_manifest_paths_and_rig_identifiers_reject_surrogates(self):
        for key in ("path", "rig_id"):
            with self.subTest(key=key):
                candidate = manifest()
                candidate["stages"][0][key] = "bad-\ud800"
                with self.assertRaisesRegex(body.BodyContractError, "UTF-8"):
                    body.validate_bassy_manifest(candidate)

    def test_nested_checksum_input_rejects_surrogate_without_leaking_encoder(self):
        with self.assertRaisesRegex(body.BodyContractError, "UTF-8"):
            body._digest({"nested": ["ok", {"\ud800": "invalid"}]})

    def test_valid_cyrillic_and_emoji_remain_accepted(self):
        value = {
            "schema": body.IDENTITY_SCHEMA,
            "agent_id": "genome-01",
            "display_name": "Данте 🐈",
            "assigned_by": "human",
        }
        self.assertEqual(body.validate_human_name(value), value)
        self.assertEqual(body.validate_chromosome(chromosome()), chromosome())


if __name__ == "__main__":
    unittest.main()
