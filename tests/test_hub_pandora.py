from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from modlab.resources.mo2_hub.pandora import read_patch, selection_entries, verify_output


class PandoraTests(unittest.TestCase):
    def test_fnis_list_cannot_be_silently_omitted(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'FNIS_Test_List'):
                verify_output(root, {'dodge': 'Dodge'}, 0, fnis_codes=['FNIS_Test_List'])
            with (root / 'Engine.log').open('a') as log:
                log.write('INFO : FNIS Mod 1 : FNIS_Test_List\n')
            self.assertTrue(verify_output(root, {'dodge': 'Dodge'}, 0, fnis_codes=['FNIS_Test_List']).hashes)

    def test_selected_patch_priority_must_match_requested_order(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'Pandora_Engine/ActiveMods.json').write_text(json.dumps([
                {'code': 'dodge', 'active': True, 'priority': 2},
                {'code': 'other', 'active': True, 'priority': 1}]))
            with (root / 'Engine.log').open('a') as log:
                log.write('INFO : Pandora Mod 1 : Other - v.1.0.0\n')
            with self.assertRaisesRegex(ValueError, 'priority'):
                verify_output(root, {'dodge': 'Dodge', 'other': 'Other'}, 0)

    def fixture(self, root):
        mesh = root / 'meshes/actors/character/behaviors/0_master.hkx'
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(bytes.fromhex('57e0e05710c0c010') + bytes(56))
        metadata = root / 'Pandora_Engine'
        metadata.mkdir()
        (metadata / 'PreviousOutput.txt').write_text(str(mesh) + '\n')
        (metadata / 'ActiveMods.json').write_text(json.dumps([{'code': 'dodge', 'active': True, 'priority': 1}]))
        (root / 'Engine.log').write_text('INFO : Pandora Mod 1 : Dodge - v.1.0.0\nINFO : Pandora Mod 2 : Pandora Base - v.1.0.0\nINFO : Mod settings saved.\n')
        return mesh

    def test_nemesis_patch_keeps_source_identity_and_name(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dodge/info.ini'
            path.parent.mkdir()
            path.write_text('name=Dodge\nauthor=Author\nsite=https://example.org\n')
            patch = read_patch(path, 'My dodge mod')
            self.assertEqual((patch.code, patch.name, patch.source_mod), ('dodge', 'Dodge', 'My dodge mod'))

    def test_pandora_code_comes_from_xml_not_directory(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'folder/info.xml'
            path.parent.mkdir()
            path.write_text('<mod code="custom"><name>Custom patch</name><author>A</author></mod>')
            self.assertEqual(read_patch(path, 'My mod').code, 'custom')

    def test_selection_does_not_silently_enable_unselected_patches(self):
        with TemporaryDirectory() as tmp:
            patches = []
            for code in ('one', 'two'):
                path = Path(tmp) / code / 'info.ini'
                path.parent.mkdir()
                path.write_text(f'name={code}\nauthor=A\nsite=https://example.org\n')
                patches.append(read_patch(path, code))
            entries = selection_entries(patches, ['two'])
            self.assertEqual([e['code'] for e in entries if e['active']], ['two'])
            with self.assertRaisesRegex(ValueError, 'no longer available'):
                selection_entries(patches, ['missing'])

    def test_completed_build_requires_outputs_and_selected_patch_receipt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mesh = self.fixture(root)
            result = verify_output(root, {'dodge': 'Dodge'}, 0)
            self.assertIn(mesh.relative_to(root).as_posix(), result.hashes)
            self.assertIsNone(result.animations)  # Count is UI-only in the supported release.
            self.assertNotIn('Engine.log', result.hashes)

    def test_fnis_aa_config_is_retained_and_malformed_config_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            config = root / 'SKSE/Plugins/fnis_aa/config.json'
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'crc': 3371961850, 'fnis_version': 'V07.06.00.0',
                                         'fnis_creature_version': 'V07.06.00.0', 'mods': []}))
            self.assertIn('SKSE/Plugins/fnis_aa/config.json', verify_output(root, {'dodge': 'Dodge'}, 0).hashes)
            config.write_text('[]')
            with self.assertRaisesRegex(ValueError, 'FNIS AA'):
                verify_output(root, {'dodge': 'Dodge'}, 0)

    def test_ui_finished_text_without_success_receipt_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'Engine.log').write_text('INFO : 2 total animations added.\nINFO : Launch finished in 1.23 seconds.\n')
            with self.assertRaisesRegex(ValueError, 'completion'):
                verify_output(root, {'dodge': 'Dodge'}, 0)

    def test_exit_zero_and_finished_message_do_not_hide_failed_edits(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            with (root / 'Engine.log').open('a') as log:
                log.write('ERROR : Patch > FAILED > node not found\n')
            with self.assertRaisesRegex(ValueError, 'FAILED'):
                verify_output(root, {'dodge': 'Dodge'}, 0)

    def test_absent_selected_patch_is_not_a_success(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'selected patch'):
                verify_output(root, {'missing': 'Missing'}, 0)

    def test_export_receipt_cannot_point_to_old_or_external_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'output'
            root.mkdir()
            self.fixture(root)
            (root / 'Pandora_Engine/PreviousOutput.txt').write_text(str(Path(tmp) / 'old.hkx'))
            with self.assertRaisesRegex(ValueError, 'outside'):
                verify_output(root, {'dodge': 'Dodge'}, 0)

    def test_missing_mesh_and_incomplete_log_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mesh = self.fixture(root)
            mesh.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                verify_output(root, {'dodge': 'Dodge'}, 0)
            self.fixture_log = root / 'Engine.log'
            self.fixture_log.write_text('INFO : Starting\n')
            with self.assertRaisesRegex(ValueError, 'completion'):
                verify_output(root, {'dodge': 'Dodge'}, 0)


if __name__ == '__main__':
    unittest.main()
