"""Exercise assignment -> custom preset -> assignment with real publication."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
import unittest
import xml.etree.ElementTree as ET

from modlab.resources.mo2_hub import body_customization as custom, character_shapes as shapes
from modlab.resources.mo2_hub.body_choices import save_default
from tests import test_hub_body_customization as custom_tests, test_hub_character_shapes as shape_tests
from tests.test_hub_character_shapes import ShapeHost, LYDIA
from tests.test_hub_obody_config import source


class MultiOutputHost(ShapeHost):
    def __init__(self, root):
        super().__init__(root)
        self.enabled=[]
    def resolvePath(self, relative):
        for name in reversed(self.enabled):
            path=self.root/'mods'/name/relative
            if path.is_file():return str(path)
        return str(self.source) if relative==shapes.OBODY_CONFIG else ''
    def allMods(self):return ['Source', *self.enabled]
    def setPriority(self, name, priority):
        if name in self.enabled:self.enabled.remove(name);self.enabled.append(name)
    def setActive(self, name, active):
        if name in self.enabled:self.enabled.remove(name)
        if active:self.enabled.append(name)
        return True


class ShapeRoundtripTests(unittest.TestCase):
    def publish_custom(self, host, number):
        root=host.root/'builds'/'custom'/str(number)
        job=custom_tests.BodyCustomizationTests().job(root)
        job.record.update(profile_path=host.profilePath(),profile='Test')
        job.save=lambda:(root/'operation.json').write_text(json.dumps(job.record))
        custom.prepare_customizer(job,'Body','Base','Lydia' if number==1 else 'Shared female','game/Data')
        path=job.executable.parent/job.record['custom_file']
        xml=ET.parse(path);xml.getroot().find('Preset/SetSlider').set('value','45');xml.write(path)
        modules={'mobase':SimpleNamespace(GuessedString=str),'PyQt6':ModuleType('PyQt6'),
            'PyQt6.QtCore':SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda delay,fn:fn()))}
        results=[]
        with patch.dict('sys.modules',modules),patch('modlab.resources.mo2_hub.skse.require_game_closed'), \
                patch('modlab.resources.mo2_hub.body_workflow.check_body_context'):
            custom.publish_preset(host,job,custom.collect_preset(job),lambda target,error:results.append(error))
        self.assertEqual([None],results)
        return job

    def test_repeated_customization_keeps_shared_shapes_and_unrelated_rules(self):
        with TemporaryDirectory() as folder:
            host=MultiOutputHost(Path(folder));original=source()
            original['blacklistedPresetsFromRandomDistribution']=['Already private']
            host.source.write_text(json.dumps(original))
            save_default(host.profilePath(),'female',dict(body='Body',preset='Slim',selected=['Body'],individual_shapes=True))
            apply=shape_tests.ShapePublicationTests().apply
            self.assertIsNone(apply(host,managed=True)[1][0][1])
            names=[]
            for number in (1,2):
                job=self.publish_custom(host,number);names.append(job.record['custom_preset'])
                self.assertIsNone(apply(host,managed=True)[1][0][1])
                config=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
                self.assertEqual({'NordRace':['Slim']},config['raceFemale'])
                self.assertEqual(['Athletic'],config['npcFormID']['skyrim.esm']['0A2C8E'])
                self.assertTrue(set(['Already private',*names]).issubset(config['blacklistedPresetsFromRandomDistribution']))
            state=shapes.load_choices(host.profilePath())
            shapes.save_choice(host.profilePath(),LYDIA,None,expected=state)
            apply(host,managed=True)
            config=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual({},config['npcFormID'])
            self.assertEqual({'NordRace':['Slim']},config['raceFemale'])
            self.assertEqual(original,json.loads(host.source.read_text()))

    def test_unrelated_actor_rule_survives_a_custom_preset_and_reapply(self):
        with TemporaryDirectory() as folder:
            host=MultiOutputHost(Path(folder));apply=shape_tests.ShapePublicationTests().apply
            apply(host);self.publish_custom(host,1);apply(host)
            config=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual(['Hulda original'],config['npcFormID']['skyrim.esm']['013BA3'])

    def test_manual_edit_to_custom_config_is_preserved_and_not_unwrapped(self):
        with TemporaryDirectory() as folder:
            host=MultiOutputHost(Path(folder));apply=shape_tests.ShapePublicationTests().apply
            apply(host);self.publish_custom(host,1)
            config=Path(host.resolvePath(shapes.OBODY_CONFIG));edited=config.read_text()+'\n';config.write_text(edited)
            with self.assertRaisesRegex(ValueError,'outside|edited|changed'):
                apply(host)
            self.assertEqual(edited,config.read_text())

    def test_legacy_receipts_recover_from_checked_originals_and_keep_both_presets(self):
        with TemporaryDirectory() as folder:
            host=MultiOutputHost(Path(folder));host.source.write_text(json.dumps(source()))
            save_default(host.profilePath(),'female',dict(body='Body',preset='Slim',selected=['Body'],individual_shapes=True))
            apply=shape_tests.ShapePublicationTests().apply;apply(host,managed=True)
            jobs=[self.publish_custom(host,1),self.publish_custom(host,2)]
            for job in jobs:
                job.record.pop('distribution_upstream');job.save()
            self.assertIsNone(apply(host,managed=True)[1][0][1])
            config=json.loads(Path(host.resolvePath(shapes.OBODY_CONFIG)).read_text())
            self.assertEqual({'NordRace':['Slim']},config['raceFemale'])
            self.assertTrue({j.record['custom_preset'] for j in jobs}.issubset(config['blacklistedPresetsFromRandomDistribution']))
            self.assertIsNotNone(self.publish_custom(host,3))

    def test_legacy_source_hash_mismatch_does_not_discard_user_rules(self):
        with TemporaryDirectory() as folder:
            host=MultiOutputHost(Path(folder));apply=shape_tests.ShapePublicationTests().apply
            apply(host);job=self.publish_custom(host,1)
            job.record.pop('distribution_upstream');job.save()
            original=job.directory/'original-obody.json';original.write_text(original.read_text()+'\n')
            current=Path(host.resolvePath(shapes.OBODY_CONFIG));before=current.read_bytes()
            with self.assertRaisesRegex(ValueError,'source|receipt|changed'):
                apply(host)
            self.assertEqual(before,current.read_bytes())
