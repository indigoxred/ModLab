import hashlib
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub.bodyslide import read_catalog, plan_build, verify_output
from modlab.resources.mo2_hub.body_setup import plan_rebuilds


def tri():
    # BodySlide body TRI: one shape, one position morph, one vertex; empty UV block.
    return (b'PIRT' + struct.pack('<HB', 1, 4) + b'Body' + struct.pack('<HB', 1, 5)
            + b'Waist' + struct.pack('<fHHhhhH', 0.001, 1, 0, 1, 2, 3, 0))


class BodyMorphTests(unittest.TestCase):
    def catalog(self, root):
        (root / 'SliderSets').mkdir()
        (root / 'SliderPresets').mkdir()
        (root / 'SliderSets/body.osp').write_text('<SliderSetInfo><SliderSet name="Body">'
            '<OutputPath>meshes/actors</OutputPath><OutputFile GenWeights="true">body</OutputFile>'
            '</SliderSet></SliderSetInfo>')
        (root / 'SliderPresets/body.xml').write_text('<SliderPresets><Preset name="Slim" set="Body"/></SliderPresets>')
        return read_catalog(root)

    def test_morph_build_requires_one_shared_tri_for_weighted_mesh_pair(self):
        with TemporaryDirectory() as directory:
            catalog = self.catalog(Path(directory))
            expected = plan_build(catalog, ['Body'], 'Slim', morphs=True)
            self.assertEqual(('meshes/actors/body_0.nif', 'meshes/actors/body_1.nif',
                              'meshes/actors/body.tri'), expected)

    def test_complete_body_tri_is_checked_and_hashed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'body.tri').write_bytes(tri())
            self.assertEqual({'body.tri': hashlib.sha256(tri()).hexdigest()}, verify_output(root, ['body.tri']))

    def test_incomplete_wrong_format_or_trailing_morph_data_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for data in (tri()[:4], tri()[:-1], b'FRTRI003' + bytes(100), tri() + b'x',
                         b'PIRT' + struct.pack('<HH', 0, 0)):
                with self.subTest(data=data):
                    (root / 'body.tri').write_bytes(data)
                    with self.assertRaises(ValueError):
                        verify_output(root, ['body.tri'])

    def test_rebuild_preserves_morph_mode_instead_of_dropping_tri(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self.catalog(root)
            record = root / 'operation.json'
            record.write_text(json.dumps({'sources': []}))
            manifest = {'projects': {'Body': {'preset': 'Slim', 'morphs': True,
                'outputs': ['meshes/actors/body_0.nif', 'meshes/actors/body_1.nif', 'meshes/actors/body.tri'],
                'build_record': str(record)}}}
            groups, issues = plan_rebuilds(catalog, manifest, [('changed',)])
            self.assertEqual([], issues)
            self.assertEqual({('Slim', True): ['Body']}, groups)

    def test_guided_mode_uses_only_selected_projects_and_requires_mixed_choice(self):
        from modlab.resources.mo2_hub import body_choices
        resolve = getattr(body_choices, 'selected_morph_mode', None)
        self.assertIsNotNone(resolve, 'Guided build must have an explicit policy independent of Advanced')
        saved = {'Body': {'morphs': True}, 'Dress': {'morphs': False}}
        self.assertTrue(resolve(['Body'], saved, None))
        self.assertFalse(resolve(['Dress'], saved, None))
        self.assertTrue(resolve(['Body', 'New Outfit'], saved, None))
        with self.assertRaisesRegex(ValueError, 'different'):
            resolve(['Body', 'Dress'], saved, None)
        self.assertTrue(resolve(['Body', 'Dress'], saved, True))
        self.assertFalse(resolve(['Body', 'Dress'], saved, False))
