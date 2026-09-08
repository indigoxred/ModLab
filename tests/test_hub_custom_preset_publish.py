import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace,ModuleType
from unittest.mock import patch
from tests import test_hub_body_customization as fixtures
from modlab.resources.mo2_hub.body_customization import prepare_customizer,collect_preset,publish_preset,VIRTUAL
import xml.etree.ElementTree as ET


class PresetHost:
    def __init__(self, root, enable=True, change_profile=False):
        self.root=root; self.enabled=False; self.allow_enable=enable; self.other=False
        self.change_profile=change_profile; self.callbacks=[]; self.target=None; self.refresh_count=0
        self.priority_changes=[]
    def modsPath(self): return str(self.root/'mods')
    def profilePath(self): return 'Other' if self.other else 'Test'
    def resolvePath(self, relative): return str(self.target/relative) if self.enabled and (self.target/relative).is_file() else ''
    def createMod(self,name):
        self.target=self.root/'mods'/str(name); self.target.mkdir(parents=True)
        return SimpleNamespace(absolutePath=lambda:str(self.target))
    def modList(self): return self
    def allMods(self): return [self.target.name]
    def priority(self,name): return 0
    def state(self,name):return 1 if self.enabled else 0
    def setPriority(self,*args):self.priority_changes.append(args)
    def setActive(self,name,value):
        if self.allow_enable: self.enabled=value
        return self.allow_enable
    def onNextRefresh(self,callback,unused): self.callbacks.append(callback)
    def refresh(self):
        self.refresh_count+=1
        if self.change_profile: self.other=True
        callbacks,self.callbacks=self.callbacks,[]
        for callback in callbacks: callback()


class CustomPresetPublicationTests(unittest.TestCase):
    def prepare(self,root,session='custom'):
        directory=root/'builds'/session; job=fixtures.BodyCustomizationTests().job(directory)
        job.record['profile']='Test'; prepare_customizer(job,'Body','Base','Female default','game/Data')
        file=job.executable.parent/job.record['custom_file']; tree=ET.parse(file)
        tree.getroot().find('Preset/SetSlider').set('value','45'); tree.write(file)
        return job,collect_preset(job)

    def publish(self,host,job,data):
        results=[]
        modules={'mobase':SimpleNamespace(GuessedString=str,ModState=SimpleNamespace(ACTIVE=1)),'PyQt6':ModuleType('PyQt6'),
            'PyQt6.QtCore':SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda delay,fn:fn()))}
        with patch.dict('sys.modules',modules),patch('modlab.resources.mo2_hub.skse.require_game_closed'),patch('modlab.resources.mo2_hub.body_workflow.check_body_context'):
            publish_preset(host,job,data,lambda path,error:results.append((path,error)))
        return results

    def test_saved_preset_becomes_effective_without_importing_preview_meshes(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); job,data=self.prepare(root); host=PresetHost(root)
            (job.directory/'preview-output/never-install.nif').write_bytes(b'preview only')
            results=self.publish(host,job,data)
            self.assertEqual([(host.target,None)],results)
            self.assertEqual(data,Path(host.resolvePath(VIRTUAL+job.record['custom_file'])).read_bytes())
            self.assertEqual([],list(host.target.rglob('*.nif')))
            self.assertEqual('Custom preset installed and effective',job.record['status'])

    def test_activation_failure_or_profile_change_never_reports_installed(self):
        for options in ({'enable':False},{'change_profile':True}):
            with self.subTest(options=options),TemporaryDirectory() as folder:
                root=Path(folder); job,data=self.prepare(root); host=PresetHost(root,**options)
                results=self.publish(host,job,data)
                self.assertTrue(results[0][1]); self.assertFalse(host.enabled)
                self.assertEqual('Custom preset needs attention',job.record['status'])

    def test_adding_another_private_preset_does_not_move_existing_output(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);host=PresetHost(root)
            job,data=self.prepare(root);self.publish(host,job,data)
            previous=list(host.priority_changes)
            second,data=self.prepare(root,'second');result=self.publish(host,second,data)
            self.assertEqual([(host.target,None)],result)
            self.assertEqual(previous,host.priority_changes)
            self.assertEqual(2,len(list(host.target.rglob('*.xml'))))

    def test_preset_changed_after_collection_is_not_published(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); job,data=self.prepare(root); host=PresetHost(root)
            file=job.executable.parent/job.record['custom_file']; file.write_bytes(data.replace(b'value="45"',b'value="46"'))
            with self.assertRaisesRegex(ValueError,'changed'): self.publish(host,job,data)
            self.assertIsNone(host.target)

    def test_obody_distribution_is_preserved_with_only_new_preset_excluded(self):
        from tests.test_hub_obody_config import source
        from modlab.resources.mo2_hub.body_customization import OBODY_CONFIG
        with TemporaryDirectory() as folder:
            root=Path(folder); job,data=self.prepare(root)
            upstream=root/'mods/OBody'/OBODY_CONFIG; upstream.parent.mkdir(parents=True)
            config=source(); config['npc']={'Lydia':['Existing shape']}
            original=json.dumps(config).encode(); upstream.write_bytes(original)
            class Host(PresetHost):
                def resolvePath(self,relative):
                    if relative==OBODY_CONFIG and not self.enabled: return str(upstream)
                    return super().resolvePath(relative)
            host=Host(root); results=self.publish(host,job,data)
            self.assertEqual([(host.target,None)],results)
            actual=json.loads(Path(host.resolvePath(OBODY_CONFIG)).read_text())
            self.assertEqual(['Zeroed Sliders',job.record['custom_preset']],actual['blacklistedPresetsFromRandomDistribution'])
            actual['blacklistedPresetsFromRandomDistribution']=config['blacklistedPresetsFromRandomDistribution']
            self.assertEqual(config,actual)
            self.assertEqual(original,upstream.read_bytes())
            self.assertEqual(original,(job.directory/'original-obody.json').read_bytes())
            second,second_data=self.prepare(root,'second')
            self.assertEqual([(host.target,None)],self.publish(host,second,second_data))
            final=json.loads(Path(host.resolvePath(OBODY_CONFIG)).read_text())
            self.assertEqual(['Zeroed Sliders',job.record['custom_preset'],second.record['custom_preset']],final['blacklistedPresetsFromRandomDistribution'])
