import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from modlab.resources.mo2_hub.bodyslide import Catalog, Project
from modlab.resources.mo2_hub.body_choices import (body_role, shared_bodies, compatible_presets,
    choice_groups, select_projects, save_default, load_defaults, verified_default)


class BodyChoiceTests(unittest.TestCase):
    def outfit_paths(self):
        return {p for name in ('Iron', 'Iron physics', 'Boots', 'Other family') for p in self.catalog().projects[name].outputs}

    def catalog(self):
        def project(name, path, groups=('Family',)):
            return Project(name, (path + '_0.nif', path + '_1.nif'), set(groups))
        assets = 'meshes/actors/character/character assets/'
        projects = {
            'Body': project('Body', assets + 'femalebody'),
            'Hands': project('Hands', assets + 'femalehands'),
            'Other hands': project('Other hands', assets + 'femalehands'),
            'Feet': project('Feet', assets + 'femalefeet'),
            'Iron': project('Iron', 'meshes/armor/iron/f/cuirass'),
            'Iron physics': project('Iron physics', 'meshes/armor/iron/f/cuirass'),
            'Boots': project('Boots', 'meshes/armor/iron/f/boots'),
            'NPC body': project('NPC body', 'meshes/actors/unique/Lydia/femalebody'),
            'Unknown': project('Unknown', 'meshes/extra/thing'),
            'Other family': project('Other family', 'meshes/armor/other/armor', ('Other',)),
        }
        return Catalog(projects, {'Shape': ('Body', {'Family'}), 'Other shape': ('', {'Other'})}, {})

    def test_old_build_cannot_verify_a_failed_new_default(self):
        request = dict(body='Body B', preset='Shape B', selected=['Body B'])
        record = dict(projects=['Body A'], preset='Shape A', hashes={'mesh': 'hash'}, effective_output_issues=[])
        self.assertFalse(verified_default(request, record, Path('old'), True))
        request.update(body='Body A', preset='Shape A', selected=['Body A'])
        self.assertFalse(verified_default(request, record, Path('old'), True))
        request['build_record'] = str(Path('old')/'operation.json')
        self.assertTrue(verified_default(request, record, Path('old'), True))
        record['effective_output_issues'] = ['Another mod wins']
        self.assertFalse(verified_default(request, record, Path('old'), True))

    def test_unknown_outfit_ownership_is_not_auto_selected(self):
        groups = choice_groups(self.catalog(), 'Body', 'Shape')
        self.assertFalse(any(g.kind == 'outfit' for g in groups))

    def test_shared_body_detection_does_not_treat_unique_npc_body_as_global(self):
        c = self.catalog()
        self.assertEqual(['Body'], shared_bodies(c, 'female'))
        self.assertIsNone(body_role(c.projects['NPC body']))
        self.assertEqual(['Shape'], compatible_presets(c, 'Body'))

    def test_outfits_exclude_unknown_private_bodies_and_unmatched_family(self):
        groups = choice_groups(self.catalog(), 'Body', 'Shape', outfit_paths=self.outfit_paths())
        names = {n for group in groups for n in group.options}
        self.assertIn('Boots', names)
        self.assertFalse({'Unknown', 'NPC body', 'Other family'} & names)
        self.assertEqual(2, len(next(g for g in groups if 'Iron' in g.options).options))

    def test_ambiguities_require_user_choice_instead_of_building_both(self):
        with self.assertRaisesRegex(ValueError, 'Choose'):
            select_projects(self.catalog(), 'Body', 'Shape', {}, outfits=True, outfit_paths=self.outfit_paths())

    def test_saved_variant_is_used_only_when_it_is_still_an_available_candidate(self):
        c = self.catalog()
        groups = choice_groups(c, 'Body', 'Shape', saved={'Hands': {}, 'Iron physics': {}}, outfit_paths=self.outfit_paths())
        chosen = {g.key: g.suggested for g in groups if g.suggested}
        selected = select_projects(c, 'Body', 'Shape', chosen, outfits=True, outfit_paths=self.outfit_paths())
        self.assertEqual({'Body', 'Hands', 'Feet', 'Iron physics', 'Boots'}, set(selected))
        del c.projects['Iron physics']
        self.assertFalse(any(g.suggested == 'Iron physics' for g in choice_groups(c, 'Body', 'Shape', saved={'Iron physics': {}}, outfit_paths=self.outfit_paths())))

    def test_body_only_leaves_outfits_out_and_explicit_skip_is_allowed(self):
        c = self.catalog()
        hands = next(g for g in choice_groups(c, 'Body', 'Shape', outfit_paths=self.outfit_paths()) if 'Hands' in g.options)
        selected = select_projects(c, 'Body', 'Shape', {hands.key: ''}, outfits=False, outfit_paths=self.outfit_paths())
        self.assertEqual({'Body', 'Feet'}, set(selected))

    def test_selected_project_cannot_be_substituted_into_another_choice(self):
        c = self.catalog()
        groups = choice_groups(c, 'Body', 'Shape', outfit_paths=self.outfit_paths())
        choices = {g.key: g.options[0] for g in groups}
        hands = next(g for g in groups if 'Hands' in g.options)
        choices[hands.key] = 'Iron'
        with self.assertRaisesRegex(ValueError, 'available'):
            select_projects(c, 'Body', 'Shape', choices, outfits=True, outfit_paths=self.outfit_paths())

    def test_defaults_are_profile_scoped_and_success_does_not_erase_other_sex(self):
        with TemporaryDirectory() as root:
            profile = Path(root)
            save_default(profile, 'male', {'body': 'Male', 'preset': 'Shape'})
            save_default(profile, 'female', {'body': 'Body', 'preset': 'Shape'})
            self.assertEqual({'male', 'female'}, set(load_defaults(profile)))
            with self.assertRaisesRegex(ValueError, 'female or male'):
                save_default(profile, 'Lydia', {})

    def test_overlapping_projects_with_different_path_sets_are_still_one_choice(self):
        c = self.catalog()
        c.projects['Bundle'] = Project('Bundle', c.projects['Iron'].outputs + c.projects['Boots'].outputs, {'Family'})
        groups = choice_groups(c, 'Body', 'Shape', outfit_paths=self.outfit_paths())
        iron = next(g for g in groups if 'Iron' in g.options)
        self.assertIn('Boots', iron.options)
        self.assertIn('Bundle', iron.options)


if __name__ == '__main__':
    unittest.main()
