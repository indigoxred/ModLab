import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

from modlab.resources.mo2_hub import character_shapes as shapes
from tests.test_hub_shape_settings import SettingsHost

LYDIA = '0A2C8E:skyrim.esm'
HULDA = '013BA3:skyrim.esm'


class CharacterShapeTests(unittest.TestCase):
    def test_character_edits_retain_other_choices_and_reject_stale_dialog(self):
        with TemporaryDirectory() as folder:
            profile = Path(folder)
            first = shapes.load_choices(profile)
            lydia = dict(name='Lydia', body='CBBE NeverNude', preset='Lydia custom')
            updated = shapes.save_choice(profile, LYDIA, lydia, expected=first)
            self.assertEqual({LYDIA: lydia}, updated['choices'])
            with self.assertRaisesRegex(ValueError, 'changed'):
                shapes.save_choice(profile, HULDA, dict(lydia, name='Hulda'), expected=first)
            result = shapes.save_choice(profile, HULDA, dict(lydia, name='Hulda'), expected=updated)
            self.assertEqual(lydia, result['choices'][LYDIA])
            result = shapes.save_choice(profile, LYDIA, None, expected=result)
            self.assertEqual([HULDA], list(result['choices']))

    def test_neutral_evidence_uses_saved_slider_values_not_preset_title(self):
        with TemporaryDirectory() as folder:
            runner = Path(folder)
            (runner/'SliderPresets').mkdir()
            path = runner/'SliderPresets/preset.xml'
            def put(value):
                path.write_text('<SliderPresets><Preset name="Zeroed Sliders" set="Body">'
                    '<SetSlider name="Waist" size="small" value="'+value+'"/>'
                    '<SetSlider name="Waist" size="big" value="0"/></Preset></SliderPresets>')
            put('15')
            self.assertFalse(shapes.neutral_preset(runner, 'Zeroed Sliders'))
            put('0')
            self.assertTrue(shapes.neutral_preset(runner, 'Zeroed Sliders'))
            put('nan')
            self.assertFalse(shapes.neutral_preset(runner, 'Zeroed Sliders'))

    def test_applied_config_is_rebuilt_from_upstream_not_accumulated_overrides(self):
        from tests.test_hub_obody_config import source
        original = json.dumps(source())
        names = {LYDIA: {'name': 'Lydia'}, HULDA: {'name': 'Hulda'}}
        available = {key: ['Athletic', 'Slim'] for key in names}
        choices = {LYDIA: dict(name='Lydia', body='Body', preset='Athletic')}
        changed = shapes.plan_assignments(original, choices, names, available)
        self.assertEqual(['Athletic'], json.loads(changed.text)['npcFormID']['skyrim.esm']['0A2C8E'])
        restored = shapes.plan_assignments(original, {}, names, available)
        self.assertEqual(source(), json.loads(restored.text))

    def test_omitted_slider_uses_project_default_and_cannot_fake_neutral_base(self):
        with TemporaryDirectory() as folder:
            runner=Path(folder);(runner/'SliderPresets').mkdir();(runner/'SliderSets').mkdir()
            (runner/'SliderPresets/base.xml').write_text('<SliderPresets><Preset name="Zero" set="Body">'
                '<SetSlider name="Waist" size="small" value="0"/><SetSlider name="Waist" size="big" value="0"/>'
                '</Preset></SliderPresets>')
            path=runner/'SliderSets/body.osp'
            path.write_text('<SliderSetInfo><SliderSet name="Body"><Slider name="Waist" small="10" big="20"/>'
                '<Slider name="Hips" small="0" big="35"/></SliderSet></SliderSetInfo>')
            self.assertFalse(shapes.neutral_preset(runner,'Zero',['Body']))
            path.write_text(path.read_text().replace('big="35"','big="0"'))
            self.assertTrue(shapes.neutral_preset(runner,'Zero',['Body']))

    def test_different_character_body_does_not_pass_shared_build_evidence(self):
        with TemporaryDirectory() as folder:
            profile = Path(folder)
            character = dict(name='Lydia', body={'sex': 'female', 'parts': [
                {'models': ['meshes/private/body_0.nif', 'meshes/private/body_1.nif']} ]})
            with self.assertRaisesRegex(ValueError, 'Prepare'):
                shapes.prepared_body(profile, character, {'body': 'CBBE NeverNude'}, lambda path: '')


class ShapeHost(SettingsHost):
    def __init__(self, root, **options):
        from tests.test_hub_obody_config import source
        super().__init__(root, **options)
        Path(self.profilePath()).mkdir(parents=True)
        self.source=self.source.parent/'OBody_presetDistributionConfig.json'
        data=source();data['npcFormID']={'skyrim.esm':{'013BA3':['Hulda original']}}
        self.source.write_text(json.dumps(data))
        state=shapes.load_choices(self.profilePath())
        shapes.save_choice(self.profilePath(),LYDIA,dict(name='Lydia',body='Body',preset='Athletic'),expected=state)


class ShapePublicationTests(unittest.TestCase):
    def apply(self,host,*,managed=False):
        results=[]
        modules={'mobase':SimpleNamespace(GuessedString=str,getFileVersion=lambda _: '1.6.1170'),
                 'PyQt6':ModuleType('PyQt6'),
                 'PyQt6.QtCore':SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda delay,fn:fn()))}
        with patch.dict('sys.modules',modules), patch('modlab.resources.mo2_hub.skse.require_game_closed'), \
                patch('modlab.resources.mo2_hub.inspection.collect_setup',return_value=SimpleNamespace(plugins=[])), \
                patch('modlab.resources.mo2_hub.shape_support.inspect_support',return_value=[]), \
                patch('modlab.resources.mo2_hub.foundations.inspect_foundations',return_value=[]), \
                patch.object(shapes,'prepared_body',return_value={}), \
                patch.object(shapes,'preset_inputs',return_value={}), \
                patch('modlab.resources.mo2_hub.body_choices.compatible_presets',return_value=['Athletic','Slim']):
            characters={LYDIA:{'name':'Lydia'},HULDA:{'name':'Hulda'}}
            catalog=None
            if managed:
                for value in characters.values():value['body']=dict(sex='female',scope='shared',race_editor='NordRace',race_body_models=['body.nif'],parts=[dict(models=['body.nif'])])
                catalog=SimpleNamespace(projects={'Body':SimpleNamespace(outputs=['body.nif'])},presets={'Athletic':{},'Slim':{}})
            record=shapes.apply_choices(host,characters,catalog,lambda r,e:results.append((r,e)))
        return record,results

    def test_character_changes_retain_shared_defaults_and_original_upstream(self):
        from tests.test_hub_obody_config import source
        from modlab.resources.mo2_hub.body_choices import save_default
        with TemporaryDirectory() as folder:
            host=ShapeHost(Path(folder));host.source.write_text(json.dumps(source()))
            save_default(host.profilePath(),'female',dict(body='Body',preset='Slim',selected=['Body'],individual_shapes=True))
            record,result=self.apply(host,managed=True)
            self.assertIsNone(result[0][1])
            config=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual({'NordRace':['Slim']},config['raceFemale'])
            self.assertEqual(['Athletic'],config['npcFormID']['skyrim.esm']['0A2C8E'])
            state=shapes.load_choices(host.profilePath())
            self.assertEqual(source(),json.loads(state['applied']['upstream']))
            shapes.save_choice(host.profilePath(),LYDIA,None,expected=state)
            self.apply(host,managed=True)
            config=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual({'NordRace':['Slim']},config['raceFemale'])
            self.assertEqual({},config['npcFormID'])

    def test_publish_verify_change_and_remove_assignment_retain_upstream_rules(self):
        with TemporaryDirectory() as folder:
            host=ShapeHost(Path(folder));source=host.source.read_bytes()
            record,result=self.apply(host)
            self.assertIsNone(result[0][1]);self.assertEqual(source,host.source.read_bytes())
            current=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual({'013BA3':['Hulda original'],'0A2C8E':['Athletic']},current['npcFormID']['skyrim.esm'])
            state=shapes.load_choices(host.profilePath())
            self.assertEqual('Assignments installed and effective',state['applied']['status'])
            state=shapes.save_choice(host.profilePath(),LYDIA,dict(name='Lydia',body='Body',preset='Slim'),expected=state)
            self.apply(host)
            current=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual(['Slim'],current['npcFormID']['skyrim.esm']['0A2C8E'])
            shapes.save_choice(host.profilePath(),LYDIA,None,expected=shapes.load_choices(host.profilePath()))
            self.apply(host)
            self.assertEqual(json.loads(source),json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text()))

    def test_failed_activation_and_profile_switch_do_not_mark_choice_applied(self):
        for options in ({'enable':False},{'switch_profile':True}):
            with self.subTest(options=options),TemporaryDirectory() as folder:
                host=ShapeHost(Path(folder),**options);profile=host.profilePath()
                record,result=self.apply(host)
                self.assertTrue(result[0][1]);self.assertIsNone(shapes.load_choices(profile)['applied'])

    def test_two_characters_share_one_owned_config_and_changed_provider_is_reported(self):
        with TemporaryDirectory() as folder:
            host=ShapeHost(Path(folder));state=shapes.load_choices(host.profilePath())
            shapes.save_choice(host.profilePath(),HULDA,dict(name='Hulda',body='Body',preset='Slim'),expected=state)
            self.assertEqual('character-shapes-pending',shapes.inspect_choices(host)[0].code)
            record,result=self.apply(host)
            self.assertIsNone(result[0][1])
            current=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual({'013BA3':['Slim'],'0A2C8E':['Athletic']},current['npcFormID']['skyrim.esm'])
            # This fixture isolates publication/VFS; binary body relationship
            # changes are exercised in test_hub_shape_body_changes.
            with patch.object(shapes,'inspect_body_choices',return_value=[]):
                self.assertEqual('character-shapes-current',shapes.inspect_choices(host)[0].code)
                host.active=False
                self.assertEqual('character-shapes-stale',shapes.inspect_choices(host)[0].code)

    def test_manual_config_edit_is_not_discarded(self):
        with TemporaryDirectory() as folder:
            host=ShapeHost(Path(folder));self.apply(host)
            current=Path(host.resolvePath(shapes.OBODY_CONFIG));edited=current.read_text()+'\n';current.write_text(edited)
            with self.assertRaisesRegex(ValueError,'edited outside'):
                self.apply(host)
            self.assertEqual(edited,current.read_text())

    def test_failed_refresh_cannot_enable_output_from_late_callback(self):
        with TemporaryDirectory() as folder:
            host=ShapeHost(Path(folder),fail_initial_refresh=True)
            record,result=self.apply(host);self.assertTrue(result[0][1])
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                host.refresh()
            self.assertFalse(host.active)
            self.assertEqual(1,len(result))
