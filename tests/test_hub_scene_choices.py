from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class SceneChoiceTests(unittest.TestCase):
    def sources(self, root):
        from modlab.resources.mo2_hub.scene_choices import scan_provider
        content = {
            'Objects': {'meshes/landscape/bridges/bridge01.nif': b'object bridge',
                        'meshes/clutter/chest.nif': b'chest',
                        'textures/landscape/roads/road.dds': b'object road'},
            'Roads': {'meshes/landscape/bridges/bridge01.nif': b'installed bridge patch',
                      'textures/landscape/roads/road.dds': b'road',
                      'textures/shared/stone.dds': b'road stone'},
            'Mountains': {'meshes/landscape/mountains/mountain01.nif': b'mountain',
                          'textures/shared/stone.dds': b'mountain stone'},
        }
        for mod, files in content.items():
            for name, value in files.items():
                path = root/mod/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
        return {mod: scan_provider(mod, root/mod) for mod in content}

    def test_choose_roads_and_mountains_without_reordering_or_copying_unrelated_objects(self):
        from modlab.resources.mo2_hub.scene_choices import plan_choices, complete_textures
        with TemporaryDirectory() as tmp:
            providers = self.sources(Path(tmp))
            plan = plan_choices(providers, {'roads': 'Roads', 'mountains': 'Mountains'})
            self.assertEqual({'meshes/landscape/bridges/bridge01.nif',
                              'textures/landscape/roads/road.dds',
                              'meshes/landscape/mountains/mountain01.nif'}, set(plan['files']))
            self.assertEqual(b'installed bridge patch', Path(plan['files']['meshes/landscape/bridges/bridge01.nif']['source']).read_bytes())
            complete_textures(plan, providers, {'meshes/landscape/bridges/bridge01.nif': ['textures/shared/stone.dds'],
                                              'meshes/landscape/mountains/mountain01.nif': []}, lambda name: None)
            self.assertEqual('Roads', plan['files']['textures/shared/stone.dds']['provider'])
            self.assertNotIn('meshes/clutter/chest.nif', plan['files'])

    def test_conflicting_shared_texture_names_the_choices_instead_of_last_one_winning(self):
        from modlab.resources.mo2_hub.scene_choices import plan_choices, complete_textures
        with TemporaryDirectory() as tmp:
            providers = self.sources(Path(tmp))
            plan = plan_choices(providers, {'roads': 'Roads', 'mountains': 'Mountains'})
            with self.assertRaisesRegex(ValueError, 'Roads.*Mountains|Mountains.*Roads'):
                complete_textures(plan, providers, {
                    'meshes/landscape/bridges/bridge01.nif': ['textures/shared/stone.dds'],
                    'meshes/landscape/mountains/mountain01.nif': ['textures/shared/stone.dds']}, lambda name: None)

    def test_missing_dependency_or_incomplete_mesh_inspection_is_not_ready(self):
        from modlab.resources.mo2_hub.scene_choices import plan_choices, complete_textures
        with TemporaryDirectory() as tmp:
            providers = self.sources(Path(tmp)); plan = plan_choices(providers, {'roads': 'Roads'})
            with self.assertRaisesRegex(ValueError, 'inspection'):
                complete_textures(plan, providers, {}, lambda name: None)
            with self.assertRaisesRegex(ValueError, 'missing texture'):
                complete_textures(plan, providers, {'meshes/landscape/bridges/bridge01.nif': ['textures/missing.dds']}, lambda name: None)

    def test_selected_texture_cannot_silently_replace_another_choices_existing_texture(self):
        from copy import deepcopy
        from modlab.resources.mo2_hub.scene_choices import plan_choices, complete_textures, file_source
        # Exercise both traversal orders: the missing texture may belong to either mesh.
        for fallback_mod in ('Roads', 'Mountains'):
            with self.subTest(fallback_mod=fallback_mod), TemporaryDirectory() as tmp:
                root = Path(tmp); providers = self.sources(root)
                del providers[fallback_mod]['files']['textures/shared/stone.dds']
                external = root/'existing.dds'; external.write_bytes(b'existing chosen appearance')
                plan = plan_choices(providers, {'roads': 'Roads', 'mountains': 'Mountains'})
                before = deepcopy(plan)
                with self.assertRaisesRegex(ValueError, 'Roads.*Mountains|Mountains.*Roads'):
                    complete_textures(plan, providers, {
                        'meshes/landscape/bridges/bridge01.nif': ['textures/shared/stone.dds'],
                        'meshes/landscape/mountains/mountain01.nif': ['textures/shared/stone.dds']},
                        lambda name: file_source(external, 'Existing setup'))
                self.assertEqual(before, plan)

    def test_matching_or_newly_supplied_shared_texture_can_complete_both_choices(self):
        from modlab.resources.mo2_hub.scene_choices import plan_choices, complete_textures, file_source
        for fallback_mod in ('Roads', 'Mountains'):
            for existing in (True, False):
                with self.subTest(fallback_mod=fallback_mod, existing=existing), TemporaryDirectory() as tmp:
                    root = Path(tmp); providers = self.sources(root)
                    del providers[fallback_mod]['files']['textures/shared/stone.dds']
                    other = 'Mountains' if fallback_mod == 'Roads' else 'Roads'
                    supplied = Path(providers[other]['files']['textures/shared/stone.dds'])
                    external = root/'existing.dds'; external.write_bytes(supplied.read_bytes())
                    plan = plan_choices(providers, {'roads': 'Roads', 'mountains': 'Mountains'})
                    complete_textures(plan, providers, {
                        'meshes/landscape/bridges/bridge01.nif': ['textures/shared/stone.dds'],
                        'meshes/landscape/mountains/mountain01.nif': ['textures/shared/stone.dds']},
                        lambda name: file_source(external, 'Existing setup') if existing else None)
                    self.assertEqual(other, plan['files']['textures/shared/stone.dds']['provider'])
                    self.assertFalse(plan['dependencies'])

    def test_external_texture_is_retained_as_dependency_and_changed_files_are_detected(self):
        from modlab.resources.mo2_hub.scene_choices import plan_choices, complete_textures, check_sources, file_source
        with TemporaryDirectory() as tmp:
            root = Path(tmp); providers = self.sources(root)
            external = root/'base.dds'; external.write_bytes(b'base texture')
            plan = plan_choices(providers, {'roads': 'Roads'})
            complete_textures(plan, providers, {'meshes/landscape/bridges/bridge01.nif': ['textures/base.dds']},
                              lambda name: file_source(external, 'Existing setup'))
            self.assertNotIn('textures/base.dds', plan['files'])
            self.assertIn('textures/base.dds', plan['dependencies'])
            check_sources(plan)
            external.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_sources(plan)

    def test_routing_excludes_characters_plugins_and_generated_lod(self):
        from modlab.resources.mo2_hub.scene_choices import category
        for name in ('meshes/actors/character/body.nif', 'textures/actors/character/skin.dds',
                     'meshes/terrain/tamriel/objects/tamriel.4.0.0.bto', 'trees.esp',
                     'textures/terrain/tamriel/trees/tamrieltreelod.dds'):
            self.assertIsNone(category(name), name)
        self.assertEqual('roads', category('Meshes\\Architecture\\Solitude\\SBridge01.nif'))
        self.assertEqual('trees', category('meshes/landscape/trees/treepine01.nif'))

    def test_roads_include_installed_dwemer_dragonbridge_and_solstheim_variants(self):
        from modlab.resources.mo2_hub.scene_choices import category
        for path in ('meshes/dungeons/dwemer/roads/dweroadstraight01.nif',
                     'meshes/dungeons/nordic/exterior/dragonbridge01.nif',
                     'textures/clutter/blackreachroad01_n.dds',
                     'textures/dlc02/landscape/dlc2road01ash01.dds'):
            self.assertEqual('roads', category(path), path)

    def test_unavailable_choice_and_path_traversal_are_not_accepted(self):
        from modlab.resources.mo2_hub.scene_choices import plan_choices, category
        with TemporaryDirectory() as tmp:
            providers = self.sources(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'no longer available'):
                plan_choices(providers, {'roads': 'Missing mod'})
            with self.assertRaises(ValueError):
                category('meshes/../textures/landscape/roads/a.dds')
