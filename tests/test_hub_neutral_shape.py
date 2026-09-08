import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modlab.resources.mo2_hub import neutral_shape
from modlab.resources.mo2_hub.bodyslide import read_catalog, plan_build
from modlab.resources.mo2_hub.character_shapes import neutral_preset


class NeutralShapeTests(unittest.TestCase):
    def fixture(self, root):
        (root/'SliderSets').mkdir(); (root/'SliderPresets').mkdir()
        (root/'SliderSets/body.osp').write_text('''<SliderSetInfo><SliderSet name="Body">
            <OutputPath>meshes/actors/character/character assets</OutputPath>
            <OutputFile GenWeights="true">femalebody</OutputFile>
            <Slider name="Waist" small="0" big="0"/>
            <Slider name="Arms" small="0" big="5"/>
            <Slider name="Underwear" small="0" big="100" zap="true"/>
            </SliderSet></SliderSetInfo>''')
        (root/'SliderPresets/base.xml').write_text('''<SliderPresets><Preset name="Athletic" set="Body">
            <SetSlider name="Waist" size="small" value="20"/>
            <SetSlider name="Waist" size="big" value="35"/>
            <SetSlider name="Underwear" size="big" value="0"/>
            </Preset></SliderPresets>''')

    def test_neutral_body_retains_zaps_and_prepares_explicit_default_values(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); self.fixture(root)
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            recipe=neutral_shape.plan(root, ['Body'], 'Athletic')
            self.assertEqual('Athletic', recipe.desired_preset)
            def values(data):
                return {(n.get('name'),n.get('size')):float(n.get('value'))
                    for n in ET.fromstring(data).find('Preset').findall('SetSlider')}
            base=values(recipe.base_xml); desired=values(recipe.assignment_xml)
            self.assertEqual(0,base['Waist','big'])
            self.assertEqual(0,base['Arms','big'])
            self.assertEqual(0,base['Underwear','big'])
            self.assertEqual(35,desired['Waist','big'])
            self.assertEqual(5,desired['Arms','big'])
            self.assertNotIn(('Underwear','big'),desired)
            self.assertEqual(before,{p:p.read_bytes() for p in before})
            neutral_shape.stage(root,recipe)
            self.assertTrue(neutral_preset(root,recipe.base_name,['Body']))
            self.assertTrue(plan_build(read_catalog(root),['Body'],recipe.base_name,morphs=True))

    def test_conflicting_unset_project_defaults_cannot_be_one_default_assignment(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); self.fixture(root)
            text=(root/'SliderSets/body.osp').read_text()
            (root/'SliderSets/outfit.osp').write_text(text.replace('name="Body"','name="Outfit"')
                .replace('femalebody','outfit').replace('big="5"','big="15"'))
            (root/'SliderGroups').mkdir()
            (root/'SliderGroups/match.xml').write_text('<SliderGroups><Group name="Family">'
                '<Member name="Body"/><Member name="Outfit"/></Group></SliderGroups>')
            path=root/'SliderPresets/base.xml'
            path.write_text(path.read_text().replace('<SetSlider name="Waist"','<Group name="Family"/><SetSlider name="Waist"',1))
            with self.assertRaisesRegex(ValueError,'Arms.*different'):
                neutral_shape.plan(root,['Body','Outfit'],'Athletic')

    def test_stage_refuses_edited_generated_file_and_retains_source(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); self.fixture(root)
            recipe=neutral_shape.plan(root,['Body'],'Athletic')
            neutral_shape.stage(root,recipe)
            path=root/'SliderPresets'/recipe.base_file
            path.write_text('outside edit')
            with self.assertRaisesRegex(ValueError,'already exists'):
                neutral_shape.stage(root,recipe)
            self.assertEqual('outside edit',path.read_text())

    def test_inverted_shape_and_duplicate_values_are_not_silently_changed(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); self.fixture(root)
            path=root/'SliderSets/body.osp'
            original=path.read_text();path.write_text(original.replace('name="Waist"','name="Waist" invert="true"'))
            with self.assertRaisesRegex(ValueError,'inverted'):
                neutral_shape.plan(root,['Body'],'Athletic')
            path.write_text(original)
            path=root/'SliderPresets/base.xml'
            path.write_text(path.read_text().replace('</Preset>','<SetSlider name="Waist" size="big" value="1"/></Preset>'))
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                neutral_shape.plan(root,['Body'],'Athletic')

    def test_source_changes_change_recipe_identity_and_nonfinite_values_fail(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root)
            a=neutral_shape.plan(root,['Body'],'Athletic')
            path=root/'SliderPresets/base.xml';path.write_text(path.read_text().replace('value="35"','value="45"'))
            b=neutral_shape.plan(root,['Body'],'Athletic')
            self.assertNotEqual(a.base_name,b.base_name)
            path.write_text(path.read_text().replace('value="45"','value="NaN"'))
            with self.assertRaisesRegex(ValueError,'finite'):
                neutral_shape.plan(root,['Body'],'Athletic')
