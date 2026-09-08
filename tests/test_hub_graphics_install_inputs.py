from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch
import json
import sys
import unittest

from modlab.resources.mo2_hub.outputs import MANIFEST, digest, output_name
from modlab.resources.mo2_hub import pgpatcher_workflow as graphics


class GraphicsInstallInputsTests(unittest.TestCase):
    def fixture(self, root):
        profile=root/'profiles/Test';profile.mkdir(parents=True)
        mods=root/'mods';mods.mkdir();data=root/'data';data.mkdir()
        generated=mods/output_name('Test',str(profile),'Graphics');generated.mkdir()
        source=mods/'New landscape';source.mkdir()
        relative='meshes/landscape/rocks/rock.nif'
        for folder,value in ((generated,b'prepared'),(source,b'new source')):
            file=folder/relative;file.parent.mkdir(parents=True);file.write_bytes(value)
        hashes={relative:digest(generated/relative)}
        manifest=dict(owner='ModLab Graphics',profile_path=str(profile),projects={},hashes=hashes,
                      updated_at='2026-09-08T10:00:00+00:00')
        (generated/MANIFEST).write_text(json.dumps(manifest))
        (profile/'modlab-graphics-choices.json').write_text(json.dumps(dict(status='applied',hashes=hashes)))
        receipt=data/'modlab/installations/receipt.json';receipt.parent.mkdir(parents=True)
        record=dict(profile_path=str(profile),mods_root=str(mods),status='Installed, enabled',
                    archive=str(root/'source.7z'),result=dict(name=source.name,path=str(source)),
                    file_verification=dict(version=1,archive_sha256='a'*64,
                        files={relative:dict(size=10,sha256=digest(source/relative))}))
        receipt.write_text(json.dumps(record))
        queue=root/'reports/install-queue/batch/operation.json';queue.parent.mkdir(parents=True)
        queued=dict(version=1,profile_path=str(profile),status='Installations completed; setup check pending',
                    started_at='2026-09-08T11:00:00+00:00',current=None,
                    items=[dict(state='Completed',record=str(receipt),archive=str(root/'source.7z'))])
        queue.write_text(json.dumps(queued))
        enabled=[True];callbacks=[]
        def set_active(name,value):enabled[0]=value;return True
        host=Obj(profilePath=lambda:str(profile),profile=lambda:Obj(name=lambda:'Test'),modsPath=lambda:str(mods),
                 getPluginDataPath=lambda:str(data),resolvePath=lambda name:str(source/name),
                 pluginList=lambda:Obj(loadOrder=lambda n:0),
                 modList=lambda:Obj(state=lambda n:1 if n!=generated.name else int(enabled[0]),
                                    setActive=set_active,getMod=lambda n:Obj(absolutePath=lambda:str(mods/n))),
                 onNextRefresh=lambda fn,unused:callbacks.append(fn),refresh=lambda:None)
        return host,enabled,source,generated,relative,receipt,record,queue,queued,callbacks

    def test_verified_new_installs_allow_saved_graphics_to_regenerate_after_restart(self):
        with TemporaryDirectory() as tmp:
            host,enabled,source,generated,relative,receipt,record,queue,queued,callbacks=self.fixture(Path(tmp))
            with patch.dict(sys.modules,{'mobase':Obj(ModState=Obj(ACTIVE=1)),
                    'PyQt6.QtCore':Obj(QTimer=Obj(singleShot=lambda delay,fn:fn()))}),patch.object(graphics,'require_game_closed'):
                called=[]
                self.assertTrue(graphics.withdraw_for_upstream(host,lambda:called.append(True)))
                self.assertFalse(enabled[0]);callbacks.pop()();self.assertEqual([True],called)
                self.assertEqual(b'prepared',(generated/relative).read_bytes())
                self.assertEqual(b'new source',(source/relative).read_bytes())

    def test_unexplained_or_stale_changes_remain_protected(self):
        for change in ('modified','other profile','finished queue','old queue','external receipt','wrong path',
                       'wrong size','wrong archive','wrong result','disabled source','unverified','modified output'):
            with self.subTest(change=change),TemporaryDirectory() as tmp:
                host,enabled,source,generated,relative,receipt,record,queue,queued,callbacks=self.fixture(Path(tmp))
                if change=='modified':(source/relative).write_bytes(b'user edited')
                if change=='other profile':queued['profile_path']='other'
                if change=='finished queue':queued['setup_record']='already checked'
                if change=='old queue':queued['started_at']='2026-09-08T09:00:00+00:00'
                if change=='external receipt':queued['items'][0]['record']=str(Path(tmp)/'external.json')
                if change=='wrong path':record['result']['path']=str(Path(tmp)/'outside')
                if change=='wrong size':record['file_verification']['files'][relative]['size']=9
                if change=='wrong archive':record['archive']='different.7z'
                if change=='wrong result':record['status']='Activation needs attention'
                if change=='disabled source':host.modList=lambda:Obj(state=lambda n:int(n==generated.name),setActive=lambda *a:None)
                if change=='unverified':record['file_verification']={}
                if change=='modified output':(generated/relative).write_bytes(b'manual edit')
                receipt.write_text(json.dumps(record));queue.write_text(json.dumps(queued))
                with patch.dict(sys.modules,{'mobase':Obj(ModState=Obj(ACTIVE=1)),
                        'PyQt6.QtCore':Obj(QTimer=Obj(singleShot=lambda delay,fn:fn()))}),patch.object(graphics,'require_game_closed'):
                    with self.assertRaises(ValueError):graphics.withdraw_for_upstream(host,lambda:None)
                self.assertTrue(enabled[0]);self.assertFalse(callbacks)

    def test_one_verified_replacement_does_not_hide_another_manual_override(self):
        with TemporaryDirectory() as tmp:
            host,enabled,source,generated,relative,receipt,record,queue,queued,callbacks=self.fixture(Path(tmp))
            other='meshes/landscape/rocks/other.nif'
            (generated/other).write_bytes(b'old');(source/other).write_bytes(b'external')
            manifest=json.loads((generated/MANIFEST).read_text());manifest['hashes'][other]=digest(generated/other)
            (generated/MANIFEST).write_text(json.dumps(manifest))
            graphics.choices_path(host).write_text(json.dumps(dict(status='applied',hashes=manifest['hashes'])))
            with patch.dict(sys.modules,{'mobase':Obj(ModState=Obj(ACTIVE=1)),
                    'PyQt6.QtCore':Obj(QTimer=Obj(singleShot=lambda delay,fn:fn()))}),patch.object(graphics,'require_game_closed'):
                with self.assertRaises(ValueError):graphics.withdraw_for_upstream(host,lambda:None)
            self.assertTrue(enabled[0])
