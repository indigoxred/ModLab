import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from modlab.resources.mo2_hub.body_customization import prepare_customizer, collect_preset
from modlab.resources.mo2_hub.bodyslide import read_catalog


class BodyCustomizationTests(unittest.TestCase):
    def job(self, root):
        runner=root/'runner'; (runner/'SliderSets').mkdir(parents=True); (runner/'SliderPresets').mkdir()
        (runner/'SliderSets/body.osp').write_text('<SliderSetInfo><SliderSet name="Body"><OutputPath>meshes/body</OutputPath><OutputFile GenWeights="true">body</OutputFile></SliderSet></SliderSetInfo>')
        (runner/'SliderPresets/base.xml').write_text('<SliderPresets><Preset name="Base" set="Body"><Group name="Family"/><SetSlider name="Waist" size="big" value="32"/><SetSlider name="Waist" size="small" value="18"/></Preset></SliderPresets>')
        (runner/'Config.xml').write_text('<Config><TargetGame>4</TargetGame></Config>')
        (runner/'BodySlide.xml').write_text('<BodySlideConfig><SelectedOutfit>Other</SelectedOutfit><SelectedPreset>Other</SelectedPreset></BodySlideConfig>')
        record={'profile_path':'Test', 'project_sources':{'Body':'Body mod'}}
        def save(): pass
        return SimpleNamespace(directory=root,executable=runner/'BodySlide x64.exe',catalog=read_catalog(runner),record=record,save=save)

    def test_customizer_clones_selected_preset_and_routes_accidental_builds_to_scratch(self):
        with TemporaryDirectory() as folder:
            job=self.job(Path(folder)); runner=job.executable.parent
            original=(runner/'SliderPresets/base.xml').read_bytes()
            prepare_customizer(job,'Body','Base','Lydia','game/Data')
            config=ET.parse(runner/'Config.xml').getroot()
            self.assertEqual(str(job.directory/'preview-output'),config.findtext('OutputDataPath'))
            settings=ET.parse(runner/'BodySlide.xml').getroot()
            self.assertEqual('Body',settings.findtext('SelectedOutfit'))
            self.assertEqual(job.record['custom_preset'],settings.findtext('SelectedPreset'))
            clone=ET.parse(runner/job.record['custom_file']).getroot().find('Preset')
            self.assertEqual(['32','18'],[s.get('value') for s in clone.findall('SetSlider')])
            self.assertEqual(original,(runner/'SliderPresets/base.xml').read_bytes())

    def test_unsaved_editor_exit_does_not_report_a_customized_shape(self):
        with TemporaryDirectory() as folder:
            job=self.job(Path(folder)); prepare_customizer(job,'Body','Base','Female default','game/Data')
            self.assertIsNone(collect_preset(job))

    def test_collects_only_the_named_preset_and_preserves_both_weight_sliders(self):
        with TemporaryDirectory() as folder:
            job=self.job(Path(folder)); prepare_customizer(job,'Body','Base','Female default','game/Data')
            path=job.executable.parent/job.record['custom_file']
            xml=ET.parse(path); xml.getroot().find('Preset/SetSlider').set('value','45'); xml.write(path)
            result=collect_preset(job)
            node=ET.fromstring(result).find('Preset')
            self.assertEqual(['45','18'],[s.get('value') for s in node.findall('SetSlider')])
            self.assertEqual(job.record['custom_preset'],node.get('name'))

    def test_changed_project_and_invalid_numeric_sliders_are_not_imported(self):
        for key,value in [('set','Unrelated body'),('slider','nan')]:
            with self.subTest(key=key),TemporaryDirectory() as folder:
                job=self.job(Path(folder)); prepare_customizer(job,'Body','Base','Lydia','game/Data')
                path=job.executable.parent/job.record['custom_file']; xml=ET.parse(path)
                if key=='slider': xml.getroot().find('Preset/SetSlider').set('value',value)
                else: xml.getroot().find('Preset').set(key,value)
                xml.write(path)
                with self.assertRaises(ValueError): collect_preset(job)

    def test_new_preview_discards_old_build_buttons_and_success_state(self):
        from modlab.resources.mo2_hub.body_customization import reset_build_result
        class Button:
            enabled=True
            def setEnabled(self,value): self.enabled=value
        dialog=SimpleNamespace(installed=Path('old-output'),applied=True,pending_default={'old':True},
            install=Button(),check=Button(),build=Button())
        reset_build_result(dialog)
        self.assertIsNone(dialog.installed); self.assertFalse(dialog.applied)
        self.assertIsNone(dialog.pending_default)
        self.assertFalse(dialog.install.enabled); self.assertFalse(dialog.check.enabled)
        self.assertTrue(dialog.build.enabled)

    def test_custom_preset_is_excluded_from_existing_random_distribution(self):
        import json
        from modlab.resources.mo2_hub.obody_config import REQUIRED,PRESET_MAPS,FORM_LISTS
        from modlab.resources.mo2_hub.body_customization import exclude_random_preset
        data={key:[] for key in REQUIRED}
        for key in (*PRESET_MAPS,*FORM_LISTS,'npcFormID'): data[key]={}
        data['blacklistedPresetsShowInOBodyMenu']=True
        data['raceFemale']={'NordRace':['Shared shape']}
        data['npcFormID']={'skyrim.esm':{'0A2C8E':['Lydia existing']}}
        data['blacklistedPresetsFromRandomDistribution']=['Already private']
        result=json.loads(exclude_random_preset(json.dumps(data),'New custom'))
        self.assertEqual(['Already private','New custom'],result['blacklistedPresetsFromRandomDistribution'])
        result['blacklistedPresetsFromRandomDistribution']=data['blacklistedPresetsFromRandomDistribution']
        self.assertEqual(data,result)

    def test_unrelated_light_plugin_rules_survive_preset_exclusion(self):
        import json
        from tests.test_hub_obody_config import source
        from modlab.resources.mo2_hub.body_customization import exclude_random_preset
        data=source(); data['npcFormID']={'Follower.esp':{'FE012ABC':['Existing shape']}}
        result=json.loads(exclude_random_preset(json.dumps(data),'Custom'))
        self.assertEqual(data['npcFormID'],result['npcFormID'])
