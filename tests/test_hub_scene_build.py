from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

import test_hub_scene_choices as fixtures


class SceneBuildTests(unittest.TestCase):
    def test_prepare_publish_and_replace_preserve_source_mods_and_recover_previous_output(self):
        from modlab.resources.mo2_hub.scene_build import prepare, publish
        from modlab.resources.mo2_hub.outputs import read_manifest
        with TemporaryDirectory() as tmp:
            root = Path(tmp); mods = root/'mods'; mods.mkdir()
            providers = fixtures.SceneChoiceTests().sources(mods)
            target = mods/'Scene output'; target.mkdir()
            original = {str(p): p.read_bytes() for p in mods.rglob('*') if p.is_file()}
            inspect = lambda meshes, directory: {name: [] for name in meshes}
            first = prepare(root, 'Profile A', providers, {'roads': 'Roads', 'mountains': 'Mountains'}, inspect, lambda name: None)
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                manifest = publish(target, first, 'Profile A')
            self.assertEqual(b'installed bridge patch', (target/'meshes/landscape/bridges/bridge01.nif').read_bytes())
            self.assertNotIn('meshes/clutter/chest.nif', manifest['hashes'])
            for path, data in original.items():
                self.assertEqual(data, Path(path).read_bytes())
            second = prepare(root, 'Profile A', providers, {'mountains': 'Mountains'}, inspect, lambda name: None)
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                changed = publish(target, second, 'Profile A')
            self.assertFalse((target/'meshes/landscape/bridges/bridge01.nif').exists())
            self.assertTrue((Path(changed['previous_output'])/'meshes/landscape/bridges/bridge01.nif').is_file())
            self.assertEqual(changed, read_manifest(target, 'Profile A', 'Scene appearance'))

    def test_changed_source_or_wrong_profile_withholds_publication(self):
        from modlab.resources.mo2_hub.scene_build import prepare, publish
        with TemporaryDirectory() as tmp:
            root = Path(tmp); mods = root/'mods'; mods.mkdir()
            providers = fixtures.SceneChoiceTests().sources(mods)
            target = mods/'Scene output'; target.mkdir()
            job = prepare(root, 'A', providers, {'roads': 'Roads'}, lambda meshes, directory: {n: [] for n in meshes}, lambda name: None)
            with self.assertRaisesRegex(ValueError, 'profile'):
                publish(target, job, 'B')
            (mods/'Roads/meshes/landscape/bridges/bridge01.nif').write_bytes(b'new variant')
            with self.assertRaisesRegex(ValueError, 'changed'):
                publish(target, job, 'A')
            self.assertEqual([], list(target.iterdir()))

    def test_failure_keeps_a_record_and_never_publishes_partial_scene(self):
        from modlab.resources.mo2_hub.scene_build import prepare
        with TemporaryDirectory() as tmp:
            root = Path(tmp); providers = fixtures.SceneChoiceTests().sources(root/'mods')
            with self.assertRaisesRegex(ValueError, 'missing texture'):
                prepare(root, 'A', providers, {'roads': 'Roads'},
                        lambda meshes, directory: {n: ['textures/missing.dds'] for n in meshes}, lambda name: None)
            records = list((root/'builds/scene').glob('*/operation.json'))
            self.assertEqual(1, len(records))
            record = json.loads(records[0].read_text())
            self.assertEqual('Needs attention', record['status'])
            self.assertEqual({'roads': 'Roads'}, record['choices'])
