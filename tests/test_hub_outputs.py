import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os

from modlab.resources.mo2_hub.outputs import publish_output, output_name, inspect_output, MANIFEST


class OutputTests(unittest.TestCase):
    def setUp(self):
        guard = patch('modlab.resources.mo2_hub.skse.require_game_closed')
        guard.start(); self.addCleanup(guard.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'mods' / 'ModLab - Adventure - BodySlide'
        self.target.mkdir(parents=True)
        self.sequence = 0

    def publish(self, projects, contents):
        self.sequence += 1
        job = self.root / 'builds' / str(self.sequence)
        (job / 'output').mkdir(parents=True)
        hashes = {}
        for name, data in contents.items():
            path = job / 'output' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        (job / 'operation.json').write_text(json.dumps({'sources': [['source.osp', 'file', 1, 2]]}))
        return publish_output(self.target, job, 'C:/profiles/Adventure', projects, hashes)

    def project(self, paths, source='CBBE', preset='Slim'):
        return {'outputs': paths, 'source_mod': source, 'preset': preset, 'sources': [['source.osp', 'file', 1, 2]]}

    def test_partial_build_retains_other_outfit_and_records_provenance(self):
        self.publish({'Dress': self.project(['meshes/dress.nif'])}, {'meshes/dress.nif': b'dress'})
        manifest = self.publish({'Boots': self.project(['meshes/boots.nif'], 'New Boots')}, {'meshes/boots.nif': b'boots'})
        self.assertEqual(b'dress', (self.target / 'meshes/dress.nif').read_bytes())
        self.assertEqual('New Boots', manifest['projects']['Boots']['source_mod'])
        self.assertEqual({'Dress', 'Boots'}, set(manifest['projects']))
        self.assertTrue((self.root / 'builds/2/previous-output' / MANIFEST).is_file())

    def test_rebuild_replaces_old_variant_and_removes_its_unmatched_weight(self):
        self.publish({'Old variant': self.project(['meshes/body_0.nif', 'meshes/body_1.nif'])},
                     {'meshes/body_0.nif': b'old0', 'meshes/body_1.nif': b'old1'})
        manifest = self.publish({'New variant': self.project(['meshes/body_0.nif'])}, {'meshes/body_0.nif': b'new'})
        self.assertNotIn('Old variant', manifest['projects'])
        self.assertFalse((self.target / 'meshes/body_1.nif').exists())
        self.assertEqual(b'old1', (self.root / 'builds/2/previous-output/meshes/body_1.nif').read_bytes())

    def test_manual_changes_are_preserved_and_prevent_replacement(self):
        self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'old'})
        (self.target / 'meshes/body.nif').write_bytes(b'manual')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'new'})
        self.assertEqual(b'manual', (self.target / 'meshes/body.nif').read_bytes())

    def test_unowned_folder_or_wrong_profile_is_not_reused(self):
        (self.target / 'unrelated.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'not a ModLab'):
            self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'new'})

    def test_invalid_paths_do_not_escape_output(self):
        with self.assertRaises(ValueError):
            publish_output(self.target, self.root, 'P', {'Bad': self.project(['../outside'])}, {'../outside': 'a'})

    def test_name_is_human_readable_and_profile_distinct(self):
        self.assertIn('ModLab', output_name('Adventure', 'C:/profiles/Adventure'))
        self.assertIn('Adventure', output_name('Adventure', 'C:/profiles/Adventure'))
        self.assertNotEqual(output_name('A:B', 'C:/profiles/A:B'), output_name('A?B', 'C:/profiles/A?B'))

    def test_replacements_only_verified_while_inputs_and_winning_files_match(self):
        self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'new'})
        current = [['source.osp', 'file', 1, 2]]
        resolve = lambda name: self.target / name
        valid, issues, _ = inspect_output(self.target, 'C:/profiles/Adventure', resolve, current)
        self.assertEqual({'meshes/body.nif'}, valid)
        self.assertEqual([], issues)
        valid, issues, _ = inspect_output(self.target, 'C:/profiles/Adventure', resolve, [])
        self.assertFalse(valid)
        self.assertIn('inputs changed', issues[0])
        valid, issues, _ = inspect_output(self.target, 'C:/profiles/Adventure', resolve, [],
                                          transformed={'meshes/body.nif'})
        self.assertFalse(valid)
        self.assertIn('inputs changed', issues[0])
        valid, issues, _ = inspect_output(self.target, 'C:/profiles/Adventure', lambda name: self.root / 'absent', current)
        self.assertFalse(valid)
        self.assertIn('overridden', issues[0])

    def test_failed_publication_restores_the_previous_collection(self):
        self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'old'})
        original = os.replace

        def fail_pending(source, destination):
            if Path(source).name == 'pending-output':
                raise OSError('simulated publication failure')
            return original(source, destination)

        with patch('modlab.resources.mo2_hub.outputs.os.replace', side_effect=fail_pending):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'new'})
        self.assertEqual(b'old', (self.target / 'meshes/body.nif').read_bytes())
        self.assertTrue((self.target / MANIFEST).is_file())


    def test_static_rebuild_removes_only_selected_projects_old_morph_file(self):
        self.publish({'Body': self.project(['meshes/body.nif', 'meshes/body.tri']),
                      'Dress': self.project(['meshes/dress.nif', 'meshes/dress.tri'])},
                     {'meshes/body.nif': b'body', 'meshes/body.tri': b'body morph',
                      'meshes/dress.nif': b'dress', 'meshes/dress.tri': b'dress morph'})
        result = self.publish({'Body': self.project(['meshes/body.nif'])}, {'meshes/body.nif': b'new static'})
        self.assertFalse((self.target / 'meshes/body.tri').exists())
        self.assertEqual(b'dress morph', (self.target / 'meshes/dress.tri').read_bytes())
        self.assertEqual(b'body morph', (Path(result['previous_output']) / 'meshes/body.tri').read_bytes())
