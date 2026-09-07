from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
from unittest.mock import patch
import json
import sys
import unittest
import zlib

from modlab.resources.mo2_hub.outputs import output_name, digest, MANIFEST


class GraphicsCycleTests(unittest.TestCase):
    def test_withdrawal_is_recoverable_and_refuses_modified_output(self):
        from modlab.resources.mo2_hub import pgpatcher_workflow as graphics
        with TemporaryDirectory() as tmp:
            root=Path(tmp); profile=root/'profile'; profile.mkdir(); mods_path=root/'mods'; mods_path.mkdir()
            name=output_name('Test',str(profile),'Graphics'); target=mods_path/name; target.mkdir()
            (target/'mesh.nif').write_bytes(b'original')
            manifest={'owner':'ModLab Graphics','profile_path':str(profile),'projects':{},
                'hashes':{'mesh.nif':digest(target/'mesh.nif')}}
            (target/MANIFEST).write_text(json.dumps(manifest))
            enabled=[True]; callbacks=[]
            def set_active(name,value): enabled[0]=value; return True
            host=Obj(profilePath=lambda:str(profile),profile=lambda:Obj(name=lambda:'Test'),
                modsPath=lambda:str(mods_path),modList=lambda:Obj(state=lambda name:int(enabled[0]),setActive=set_active),
                onNextRefresh=lambda callback,unused:callbacks.append(callback),refresh=lambda:None)
            modules={'mobase':Obj(ModState=Obj(ACTIVE=1)),'PyQt6.QtCore':Obj(QTimer=Obj(singleShot=lambda delay,action:action()))}
            called=[]
            with patch.dict(sys.modules,modules), patch.object(graphics,'require_game_closed'):
                self.assertTrue(graphics.withdraw_for_upstream(host,lambda:called.append(True)))
                self.assertFalse(enabled[0]); callbacks.pop()(); self.assertEqual([True],called)
                self.assertTrue(graphics.withdrawn_path(host).is_file())
                self.assertFalse(graphics.withdraw_for_upstream(host,lambda:None))
                (target/'mesh.nif').write_bytes(b'manual change')
                with self.assertRaisesRegex(ValueError,'changed'):
                    graphics.withdraw_for_upstream(host,lambda:None)
                self.assertFalse(enabled[0])

    def test_downstream_output_never_changes_the_upstream_patch_signature(self):
        from modlab.resources.mo2_hub.synthesis_workflow import input_state
        with TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'Data').mkdir(); profile = str(root/'profile')
            graphics = output_name('Test', profile, 'Graphics')
            synthesis = output_name('Test', profile, 'Synthesis')
            names = ['Skyrim.esm', 'Synthesis.esp', 'PG_1.esp']
            origins = dict(zip(names, ['Game', synthesis, graphics]))
            for name in names: (root/'Data'/name).write_text(name)
            plugins = Obj(pluginNames=lambda:names, loadOrder=names.index, priority=names.index, origin=origins.get)
            mods = Obj(allMods=lambda:(), priority=lambda n:0)
            host = Obj(pluginList=lambda:plugins, modList=lambda:mods, profilePath=lambda:profile,
                profile=lambda:Obj(name=lambda:'Test'),
                managedGame=lambda:Obj(gameDirectory=lambda:Obj(absolutePath=lambda:str(root))),
                resolvePath=lambda n:str(root/'Data'/n))
            with patch.dict(sys.modules, {'mobase':Obj(ModState=Obj(ACTIVE=1))}):
                upstream = input_state(host, ['Synthesis.esp'])
                downstream = input_state(host, (), output_mod=graphics)
            self.assertEqual(['Skyrim.esm'], upstream['order'])
            self.assertEqual(['Skyrim.esm','Synthesis.esp'], downstream['order'])

    def test_graphics_transformation_requires_the_exact_body_and_effective_graphics_bytes(self):
        from modlab.resources.mo2_hub.pgpatcher import transformed_body_files
        with TemporaryDirectory() as tmp:
            root=Path(tmp); body=root/'body'; graphics=root/'graphics'
            name='meshes/body.nif'
            for path, data in ((body/name,b'body mesh'),(graphics/name,b'patched mesh')):
                path.parent.mkdir(parents=True); path.write_bytes(data)
            receipt={'meshes\\body.nif':{'crc32original':zlib.crc32(b'body mesh'),
                'crc32patched':zlib.crc32(b'patched mesh')}}
            (graphics/'ParallaxGen_Diff.json').write_text(json.dumps(receipt))
            body_manifest={'hashes':{name:digest(body/name)}}
            graphics_manifest={'hashes':{name:digest(graphics/name),
                'ParallaxGen_Diff.json':digest(graphics/'ParallaxGen_Diff.json')}}
            resolve=lambda n:str(graphics/n)
            self.assertEqual({name}, transformed_body_files(body,body_manifest,graphics,graphics_manifest,resolve))
            (body/name).write_bytes(b'new body mesh')
            self.assertEqual(set(), transformed_body_files(body,body_manifest,graphics,graphics_manifest,resolve))
            (body/name).write_bytes(b'body mesh')
            (graphics/name).write_bytes(b'edited result')
            self.assertEqual(set(), transformed_body_files(body,body_manifest,graphics,graphics_manifest,resolve))

    def test_pending_graphics_choice_survives_failure_without_replacing_previous_success(self):
        from modlab.resources.mo2_hub import pgpatcher_workflow as graphics
        with TemporaryDirectory() as tmp:
            host=Obj(profilePath=lambda:tmp)
            graphics.choices_path(host).write_text(json.dumps({'status':'applied','choices':{'renderer':'ENB'}}))
            request=graphics.begin_pending(host, {'renderer':'Community Shaders','pbr':True})
            graphics.fail_pending(host, request['id'], 'Missing renderer')
            self.assertEqual('Missing renderer',graphics.load_pending(host)['error'])
            self.assertEqual('ENB',graphics.load_choices(host)['choices']['renderer'])
            with self.assertRaisesRegex(ValueError, 'changed'):
                graphics.clear_pending(host, 'other-request')
            graphics.clear_pending(host,request['id'])
            self.assertIsNone(graphics.load_pending(host))
