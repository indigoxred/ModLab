import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from modlab.resources.mo2_hub import author_neutral
from modlab.resources.mo2_hub.bodyslide import read_catalog
from modlab.resources.mo2_hub.character_shapes import neutral_preset


class AuthorNeutralTests(unittest.TestCase):
    def fixture(self,root):
        (root/'SliderSets').mkdir();(root/'SliderPresets').mkdir()
        (root/'SliderSets/body.osp').write_text('<SliderSetInfo><SliderSet name="Body">'
            '<OutputFile GenWeights="true">body</OutputFile>'
            '<Slider name="Fit" small="100" big="0" hidden="true"/>'
            '<Slider name="Waist" small="0" big="0"/></SliderSet></SliderSetInfo>')
        path=root/'SliderPresets/author.xml'
        path.write_text('<SliderPresets><Preset name="Author Neutral" set="Body">'
            '<SetSlider name="Fit" size="small" value="100"/>'
            '<SetSlider name="Waist" size="big" value="0"/></Preset></SliderPresets>')
        fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()
        return path,{fingerprint:dict(preset='Author Neutral',mod='Fixture body',version='1.0',guide='https://example.test/setup')}

    def test_verified_author_corrections_are_kept_instead_of_zeroed(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);path,known=self.fixture(root);before=path.read_bytes()
            with patch.dict(author_neutral.KNOWN,known,clear=True):
                proof=author_neutral.inspect(root,'Author Neutral',['Body'])
                self.assertEqual('1.0',proof['version'])
                self.assertTrue(neutral_preset(root,'Author Neutral',['Body']))
            self.assertEqual(before,path.read_bytes())

    def test_same_preset_title_with_modified_bytes_is_not_author_verified(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);path,known=self.fixture(root)
            path.write_text(path.read_text().replace('value="0"','value="35"'))
            with patch.dict(author_neutral.KNOWN,known,clear=True):
                self.assertIsNone(author_neutral.inspect(root,'Author Neutral',['Body']))
                self.assertFalse(neutral_preset(root,'Author Neutral',['Body']))

    def test_selection_must_match_author_family_metadata(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);path,known=self.fixture(root)
            (root/'SliderSets/other.osp').write_text('<SliderSetInfo><SliderSet name="Other"><OutputFile>other</OutputFile></SliderSet></SliderSetInfo>')
            with patch.dict(author_neutral.KNOWN,known,clear=True):
                self.assertIsNone(author_neutral.inspect(root,'Author Neutral',['Other']))

    def test_recommendation_reports_release_and_effective_source(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);path,known=self.fixture(root)
            files={p.relative_to(root).as_posix():p for p in root.rglob('*') if p.is_file()}
            with patch.dict(author_neutral.KNOWN,known,clear=True):
                proof=author_neutral.recommend(files,read_catalog(root),'Body')
            self.assertEqual('Author Neutral',proof['preset'])
            self.assertEqual('SliderPresets/author.xml',proof['file'])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),proof['sha256'])
