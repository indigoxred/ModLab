from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub.bodyslide import read_catalog, plan_build, verify_output
from modlab.resources.mo2_hub.body_workflow import effective_files, readable_path


class BodySlideTests(unittest.TestCase):
    def test_mo2_findfiles_physical_results_are_not_resolved_twice(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'mods/BodySlide/CalienteTools/BodySlide'
            root.mkdir(parents=True)
            path = root / 'BodySlide.exe'
            path.write_bytes(b'fixture')
            class Host:
                def findFiles(self, folder, filters):
                    return [str(path)]
                def resolvePath(self, name):
                    return ''
                def listDirectories(self, path):
                    return []
            self.assertEqual({'BodySlide.exe': readable_path(path)}, effective_files(Host()))

    def catalogue(self, root, extra=''):
        (root / 'SliderSets').mkdir()
        (root / 'SliderGroups').mkdir()
        (root / 'SliderPresets').mkdir()
        (root / 'SliderSets/body.osp').write_text('''<SliderSetInfo><SliderSet name="Body">
            <OutputPath>meshes\\actors</OutputPath><OutputFile GenWeights="true">body</OutputFile>
            </SliderSet>''' + extra + '</SliderSetInfo>')
        (root / 'SliderGroups/family.xml').write_text('''<SliderGroups><Group name="Family">
            <Member name="Body"/><Member name="Alternative"/></Group></SliderGroups>''')
        (root / 'SliderPresets/preset.xml').write_text('''<SliderPresets>
            <Preset name="Slim" set="Body"><Group name="Family"/></Preset>
            <Preset name="Other"><Group name="Other Family"/></Preset></SliderPresets>''')
        return read_catalog(root)

    def test_weighted_projects_expect_both_meshes(self):
        with TemporaryDirectory() as directory:
            catalog = self.catalogue(Path(directory))
            self.assertEqual(('meshes/actors/body_0.nif', 'meshes/actors/body_1.nif'),
                             plan_build(catalog, ['Body'], 'Slim'))

    def test_alternative_projects_sharing_output_require_a_choice(self):
        with TemporaryDirectory() as directory:
            catalog = self.catalogue(Path(directory), '''<SliderSet name="Alternative">
                <OutputPath>meshes/actors</OutputPath><OutputFile GenWeights="true">body</OutputFile></SliderSet>''')
            with self.assertRaisesRegex(ValueError, 'same output'):
                plan_build(catalog, ['Body', 'Alternative'], 'Slim')

    def test_unrelated_preset_is_not_silently_accepted(self):
        with TemporaryDirectory() as directory:
            catalog = self.catalogue(Path(directory))
            with self.assertRaisesRegex(ValueError, 'preset'):
                plan_build(catalog, ['Body'], 'Other')

    def test_output_path_cannot_escape_the_generated_mod(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.catalogue(root)
            path = root / 'SliderSets/body.osp'
            path.write_text(path.read_text().replace('meshes\\actors', '../outside'))
            with self.assertRaisesRegex(ValueError, 'output path'):
                read_catalog(root)

    def test_duplicate_project_names_are_ambiguous(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Duplicate project'):
                self.catalogue(Path(directory), '''<SliderSet name="Body">
                    <OutputPath>meshes</OutputPath><OutputFile GenWeights="false">other</OutputFile></SliderSet>''')

    def test_missing_or_invalid_mesh_is_not_a_successful_build(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Missing'):
                verify_output(root, ['meshes/body.nif'])
            (root / 'meshes').mkdir()
            (root / 'meshes/body.nif').write_bytes(b'not a mesh')
            with self.assertRaisesRegex(ValueError, 'Invalid'):
                verify_output(root, ['meshes/body.nif'])

    def test_valid_outputs_receive_content_hashes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'meshes').mkdir()
            (root / 'meshes/body.nif').write_bytes(b'Gamebryo File Format, Version 20.2.0.7\n' + bytes(200))
            result = verify_output(root, ['meshes/body.nif'])
            self.assertEqual(64, len(result['meshes/body.nif']))

    def test_empty_selection_is_not_a_successful_noop(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Select'):
                plan_build(self.catalogue(Path(directory)), [], 'Slim')
