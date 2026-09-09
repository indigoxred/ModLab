from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub import scene_choices as scene


class SharedSceneTests(unittest.TestCase):
    def test_one_group_decision_maps_to_each_texture_identity_without_blank_defaults(self):
        from modlab.resources.mo2_hub.scene_shared import group_conflicts, choose_groups
        conflicts = [dict(path='textures/a.dds', groups=['trees', 'landscape'], evidence='first',
                         options=[dict(provider='Ground', kind='installed', sha256='a-ground'),
                                  dict(provider='Trees', kind='bundled', sha256='a-tree')]),
                     dict(path='textures/b.dds', groups=['trees', 'landscape'], evidence='second',
                         options=[dict(provider='Ground', kind='installed', sha256='b-ground'),
                                  dict(provider='Trees', kind='bundled', sha256='b-tree')])]
        groups = group_conflicts(conflicts)
        self.assertEqual(1, len(groups))
        with self.assertRaises(ValueError):
            choose_groups(groups, [None])
        selected = choose_groups(groups, [('Ground', 'installed')])
        self.assertEqual({'textures/a.dds': dict(provider='Ground', sha256='a-ground', evidence='first'),
                          'textures/b.dds': dict(provider='Ground', sha256='b-ground', evidence='second')}, selected)

    def fixture(self, root, bundled=True):
        mesh, texture = 'meshes/landscape/trees/pine.nif', 'textures/landscape/dirt.dds'
        content = {'Trees A': {mesh: b'pine'}, 'Ground C': {texture: b'current ground'},
                   'Ground D': {texture: b'explicit ground'}}
        if bundled: content['Trees A'][texture] = b'bundled ground'
        for mod, files in content.items():
            for name, value in files.items():
                path = root/mod/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
        providers = {mod: scene.scan_provider(mod, root/mod) for mod in content}
        resolve = lambda name: scene.provider_file(providers['Ground C'], name)
        return providers, mesh, texture, resolve

    def test_trees_do_not_silently_replace_retained_ground_and_user_can_keep_it(self):
        with TemporaryDirectory() as tmp:
            providers, mesh, texture, resolve = self.fixture(Path(tmp))
            plan = scene.plan_choices(providers, {'trees': 'Trees A'})
            with self.assertRaises(ValueError) as caught:
                scene.complete_textures(plan, providers, {mesh: [texture]}, resolve)
            self.assertNotIn(texture, plan['files'])
            conflict = caught.exception.conflicts[0]
            self.assertEqual({'trees', 'landscape'}, set(conflict['groups']))
            option = next(row for row in conflict['options'] if row['provider'] == 'Ground C')
            selection = {texture: dict(provider='Ground C', sha256=option['sha256'], evidence=conflict['evidence'])}
            scene.complete_textures(plan, providers, {mesh: [texture]}, resolve, texture_choices=selection)
            self.assertNotIn(texture, plan['files'])
            self.assertEqual('Ground C', plan['dependencies'][texture]['provider'])

    def test_explicit_ground_choice_is_not_rejected_for_differing_from_incidental_winner(self):
        with TemporaryDirectory() as tmp:
            providers, mesh, texture, resolve = self.fixture(Path(tmp), bundled=False)
            plan = scene.plan_choices(providers, {'trees': 'Trees A', 'landscape': 'Ground D'})
            scene.complete_textures(plan, providers, {mesh: [texture]}, resolve)
            self.assertEqual('Ground D', plan['files'][texture]['provider'])
            self.assertNotIn(texture, plan['dependencies'])

    def test_explicit_shared_override_is_saved_and_changed_options_require_a_new_decision(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); providers, mesh, texture, resolve = self.fixture(root)
            plan = scene.plan_choices(providers, {'trees': 'Trees A'})
            with self.assertRaises(ValueError) as caught:
                scene.complete_textures(plan, providers, {mesh: [texture]}, resolve)
            conflict = caught.exception.conflicts[0]
            option = next(row for row in conflict['options'] if row['provider'] == 'Trees A')
            selection = {texture: dict(provider='Trees A', sha256=option['sha256'], evidence=conflict['evidence'])}
            scene.complete_textures(plan, providers, {mesh: [texture]}, resolve, texture_choices=selection)
            self.assertEqual('Trees A', plan['files'][texture]['provider'])
            self.assertEqual(selection, plan['texture_choices'])
            (root/'Ground C'/texture).write_bytes(b'new user ground selection')
            fresh = scene.plan_choices(providers, {'trees': 'Trees A'})
            with self.assertRaises(ValueError):
                scene.complete_textures(fresh, providers, {mesh: [texture]}, resolve, texture_choices=selection)
            self.assertNotIn(texture, fresh['files'])
