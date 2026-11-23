"""Deterministic offline synthetic fixtures; no external assets or solver."""
import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from mojum.garment_contract import (
    GARMENT_SCHEMA, GarmentContractError, equip, load_garment, new_wardrobe,
    rebind_wardrobe, unequip, validate_garment, validate_wardrobe,
    verify_mesh_file,
)


def glb():
    contents = b'{"asset":{"version":"2.0"}}'
    contents += b' ' * (-len(contents) % 4)
    return struct.pack('<4sII', b'glTF', 2, 20 + len(contents)) + struct.pack('<I4s', len(contents), b'JSON') + contents


def garment(slot='neck', garment_id='scarf'):
    blob = glb()
    return {
        'schema': GARMENT_SCHEMA,
        'garment_id': garment_id, 'slot': slot,
        'source': {'origin': 'procedural-synthetic', 'license_spdx': 'CC0-1.0', 'attribution': 'Synthetic cloth fixture'},
        'mesh': {'path': 'cloth/scarf.glb', 'sha256': hashlib.sha256(blob).hexdigest(),
                 'bytes': len(blob), 'vertex_count': 8},
        'rig': {'skeleton_version': 'rig-r1', 'body_profile': 'neutral_humanoid_fixture/1',
                'age_min': 6, 'age_max': 16},
        'material': {'mass_g': 120, 'thickness_um': 1000, 'stretch_milli': 400,
                     'bend_milli': 300, 'damping_milli': 50},
        'anchors': [{'vertex_index': 0, 'joint_id': 'neck', 'weight_milli': 600},
                    {'vertex_index': 0, 'joint_id': 'spine', 'weight_milli': 400}],
    }


class GarmentContractTests(unittest.TestCase):
    def test_detached_validated_manifest(self):
        src = garment()
        out = validate_garment(src)
        out['material']['mass_g'] = 42
        self.assertEqual(src['material']['mass_g'], 120)

    def test_schema_discriminators_reject_non_string_equality_spoofing(self):
        class PretendInt(int):
            def __eq__(self, other):
                return other in (GARMENT_SCHEMA, "mojum.cloth.wardrobe/1")

            def __ne__(self, other):
                return not self.__eq__(other)

        for payload, validate in (
            (garment(), validate_garment),
            (new_wardrobe("rig-r1", "neutral_humanoid_fixture/1", 12), validate_wardrobe),
        ):
            with self.subTest(schema=payload["schema"]):
                payload["schema"] = PretendInt(7)
                with self.assertRaisesRegex(GarmentContractError, "schema"):
                    validate(payload)

    def test_origin_discriminator_rejects_hostile_equality(self):
        class HostileOrigin:
            def __eq__(self, other):
                raise RuntimeError("untrusted equality executed")

        class EqualitySpoof:
            def __eq__(self, other):
                return True

        for origin in (HostileOrigin(), EqualitySpoof(), 0, None):
            with self.subTest(origin=type(origin).__name__):
                payload = garment()
                payload["source"]["origin"] = origin
                with self.assertRaisesRegex(GarmentContractError, "origin"):
                    validate_garment(payload)

    def test_invalid_manifest_fail_closed(self):
        edits = [
            lambda x: x.update(schema='unversioned'),
            lambda x: x.update(unexpected='unknown'),
            lambda x: x.update(slot='hardware'),
            lambda x: x['mesh'].update(path='../secret.glb'),
            lambda x: x['mesh'].update(path='C:/secret.glb'),
            lambda x: x['mesh'].update(path='cloth/../secret.glb'),
            lambda x: x['mesh'].update(path='cloth\\scarf.glb'),
            lambda x: x['mesh'].update(sha256='0' * 63),
            lambda x: x['mesh'].update(bytes=True),
            lambda x: x['mesh'].update(vertex_count=0),
            lambda x: x['rig'].update(age_min=17),
            lambda x: x['rig'].update(age_min=16, age_max=6),
            lambda x: x['material'].update(mass_g=-1),
            lambda x: x['material'].update(damping_milli=float('nan')),
            lambda x: x['anchors'].append(copy.deepcopy(x['anchors'][0])),
            lambda x: x['anchors'][0].update(weight_milli=601),
            lambda x: x['anchors'][0].update(vertex_index=8),
            lambda x: x['source'].update(attribution='\ud800'),
            lambda x: x['source'].update(license_spdx=''),
        ]
        for edit in edits:
            obj = garment()
            edit(obj)
            with self.subTest(edit=repr(edit)), self.assertRaises(GarmentContractError):
                validate_garment(obj)

    def test_file_integrity_and_sandbox(self):
        data = garment()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'cloth').mkdir()
            file = root / 'cloth' / 'scarf.glb'
            file.write_bytes(glb())
            self.assertTrue(verify_mesh_file(data, root))
            file.write_bytes(glb()[:-1] + b'X')
            with self.assertRaisesRegex(GarmentContractError, 'SHA-256'):
                verify_mesh_file(data, root)
            file.write_bytes(b'X' + glb()[1:])
            with self.assertRaisesRegex(GarmentContractError, 'header'):
                verify_mesh_file(data, root)
            file.write_bytes(glb()[:-1])
            with self.assertRaisesRegex(GarmentContractError, 'size'):
                verify_mesh_file(data, root)
            file.unlink()
            try:
                file.symlink_to(root / 'target.glb')
            except (NotImplementedError, OSError):
                self.skipTest('symlinks unavailable')
            with self.assertRaisesRegex(GarmentContractError, 'symlink'):
                verify_mesh_file(data, root)

    def test_missing_asset_root_raises_contract_error(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / 'missing'
            with self.assertRaisesRegex(GarmentContractError, 'asset root'):
                verify_mesh_file(garment(), missing)
            regular_file = Path(folder) / 'not-a-directory'
            regular_file.write_bytes(b'fixture')
            with self.assertRaisesRegex(GarmentContractError, 'not a directory'):
                verify_mesh_file(garment(), regular_file)

    def test_missing_or_unreadable_manifest_is_contract_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GarmentContractError, 'manifest'):
                load_garment(Path(folder) / 'missing.json')
            with self.assertRaisesRegex(GarmentContractError, 'manifest'):
                load_garment(Path(folder))

    def test_manifest_file_duplicate_keys_and_size_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'manifest.json'
            path.write_text(json.dumps(garment()), encoding='utf-8')
            self.assertEqual(load_garment(path), garment())
            path.write_text('{"schema":"one","schema":"two"}', encoding='utf-8')
            with self.assertRaises(GarmentContractError):
                load_garment(path)
            path.write_bytes(b' ' * 32769)
            with self.assertRaisesRegex(GarmentContractError, 'budget'):
                load_garment(path)

    def test_revision_and_slot_exclusivity_without_mutation(self):
        initial = new_wardrobe('rig-r1', 'neutral_humanoid_fixture/1', 12)
        before = copy.deepcopy(initial)
        next_state = equip(initial, garment(), expected_revision=0)
        self.assertEqual(initial, before)
        self.assertEqual(next_state['revision'], 1)
        self.assertEqual(next_state['worn'][0]['slot'], 'neck')
        with self.assertRaisesRegex(GarmentContractError, 'stale'):
            unequip(next_state, 'scarf', expected_revision=0)
        with self.assertRaisesRegex(GarmentContractError, 'occupied'):
            equip(next_state, garment(garment_id='another-scarf'), expected_revision=1)
        gone = unequip(next_state, 'scarf', expected_revision=1)
        self.assertEqual(gone['worn'], [])
        self.assertEqual(gone['revision'], 2)
        self.assertEqual(next_state['revision'], 1)
        with self.assertRaisesRegex(GarmentContractError, 'not worn'):
            unequip(gone, 'scarf', expected_revision=2)

    def test_rebind_age_safety_and_manifest_evidence(self):
        original = garment()
        state = equip(new_wardrobe('rig-r1', 'neutral_humanoid_fixture/1', 8),
                      original, expected_revision=0)
        before = copy.deepcopy(state)
        narrow = garment()
        narrow['rig']['age_max'] = 8
        with self.assertRaisesRegex(GarmentContractError, 'incompatible'):
            rebind_wardrobe(state, 'rig-r1', 'neutral_humanoid_fixture/1', 9,
                            {'scarf': narrow}, expected_revision=1)
        with self.assertRaisesRegex(GarmentContractError, 'incompatible'):
            rebind_wardrobe(state, 'rig-r2', 'neutral_humanoid_fixture/1', 8,
                            {'scarf': original}, expected_revision=1)
        with self.assertRaisesRegex(GarmentContractError, 'registry'):
            rebind_wardrobe(state, 'rig-r1', 'neutral_humanoid_fixture/1', 8,
                            {}, expected_revision=1)
        mismatched = garment()
        mismatched['mesh']['sha256'] = '0' * 64
        with self.assertRaisesRegex(GarmentContractError, 'mismatch'):
            rebind_wardrobe(state, 'rig-r1', 'neutral_humanoid_fixture/1', 8,
                            {'scarf': mismatched}, expected_revision=1)
        self.assertEqual(state, before)
        updated = rebind_wardrobe(state, 'rig-r1', 'neutral_humanoid_fixture/1', 9,
                                  {'scarf': original}, expected_revision=1)
        self.assertEqual(updated['rig']['age_years'], 9)
        self.assertEqual(updated['worn'], state['worn'])
        self.assertEqual(updated['revision'], 2)

    def test_invalid_existing_state_and_revisions(self):
        state = new_wardrobe('rig-r1', 'neutral_humanoid_fixture/1', 12)
        for invalid in (True, -1, 2**63, '0'):
            with self.subTest(value=invalid), self.assertRaises(GarmentContractError):
                equip(state, garment(), expected_revision=invalid)
        corrupted = copy.deepcopy(state)
        corrupted['worn'] = [{'garment_id': 'a', 'slot': 'neck', 'sha256': '0' * 64},
                             {'garment_id': 'b', 'slot': 'neck', 'sha256': '1' * 64}]
        with self.assertRaisesRegex(GarmentContractError, 'duplicate'):
            validate_wardrobe(corrupted)
        corrupted = copy.deepcopy(state)
        corrupted['revision'] = 2**63 - 1
        with self.assertRaisesRegex(GarmentContractError, 'exhausted'):
            equip(corrupted, garment(), expected_revision=2**63 - 1)


if __name__ == '__main__':
    unittest.main()
