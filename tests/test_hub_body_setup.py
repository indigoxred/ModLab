import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from modlab.resources.mo2_hub.bodyslide import Catalog, Project, read_catalog_files
from modlab.resources.mo2_hub.body_setup import inspect_body_setup, plan_rebuilds


class BodySetupTests(unittest.TestCase):
    def test_setup_without_projects_does_not_require_bodyslide(self):
        host = SimpleNamespace(listDirectories=lambda _: [], findFiles=lambda *args: [])
        result = inspect_body_setup(host)
        self.assertEqual({}, result.groups)
        self.assertEqual((), result.findings)

    def catalog(self):
        return Catalog({
            'Body': Project('Body', ('meshes/body.nif',), {'Family'}),
            'Outfit': Project('Outfit', ('meshes/outfit.nif',), {'Family'}),
        }, {'Slim': ('Body', {'Family'}), 'Outfit Slim': ('Outfit', {'Family'})}, {})

    def manifest(self, record):
        return {'projects': {
            'Body': {'outputs': ['meshes/body.nif'], 'preset': 'Slim', 'build_record': str(record)},
            'Outfit': {'outputs': ['meshes/outfit.nif'], 'preset': 'Outfit Slim', 'build_record': str(record)},
        }}

    def test_changed_inputs_rebuild_saved_projects_with_their_own_presets(self):
        with TemporaryDirectory() as directory:
            record = Path(directory) / 'operation.json'
            record.write_text(json.dumps({'sources': [['old']]}))
            groups, issues = plan_rebuilds(self.catalog(), self.manifest(record), (('new',),))
        self.assertEqual({'Slim': ['Body'], 'Outfit Slim': ['Outfit']}, groups)
        self.assertEqual([], issues)

    def test_unchanged_input_does_not_rebuild(self):
        with TemporaryDirectory() as directory:
            record = Path(directory) / 'operation.json'
            record.write_text(json.dumps({'sources': [['same']]}))
            self.assertEqual(({}, []), plan_rebuilds(self.catalog(), self.manifest(record), (('same',),)))

    def test_removed_project_or_changed_output_requires_a_new_choice(self):
        with TemporaryDirectory() as directory:
            record = Path(directory) / 'operation.json'
            record.write_text(json.dumps({'sources': []}))
            catalog = self.catalog()
            del catalog.projects['Body']
            catalog.projects['Outfit'].outputs = ('meshes/different.nif',)
            groups, issues = plan_rebuilds(catalog, self.manifest(record), ())
        self.assertEqual({}, groups)
        self.assertEqual(2, len(issues))
        self.assertIn('Body', issues[0])
        self.assertIn('Outfit', issues[1])

    def test_new_incompatible_preset_declaration_does_not_rebuild(self):
        with TemporaryDirectory() as directory:
            record = Path(directory) / 'operation.json'
            record.write_text(json.dumps({'sources': []}))
            catalog = self.catalog()
            catalog.presets['Slim'] = ('Other', {'Other'})
            groups, issues = plan_rebuilds(catalog, self.manifest(record), (('changed',),))
        self.assertNotIn('Slim', groups)
        self.assertTrue(any('Slim' in issue for issue in issues))

    def test_catalog_uses_effective_files_from_multiple_mods_without_copying(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project.osp'
            preset = root / 'preset.xml'
            project.write_text('<SliderSetInfo><SliderSet name="Body"><OutputPath>meshes</OutputPath>'
                               '<OutputFile>body</OutputFile></SliderSet></SliderSetInfo>')
            preset.write_text('<SliderPresets><Preset name="Slim" set="Body"/></SliderPresets>')
            catalog = read_catalog_files({'SliderSets/Body/project.osp': project,
                                          'SliderPresets/preset.xml': preset})
        self.assertEqual('SliderSets/Body/project.osp', catalog.projects['Body'].source_file)
        self.assertIn('Slim', catalog.presets)
