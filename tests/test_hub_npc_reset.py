import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace,ModuleType
from unittest.mock import patch
from modlab.resources.mo2_hub.npc_reset import reset_choices,appearance_selection,KEEP_SAVED
from modlab.resources.mo2_hub.outputs import digest
from modlab.resources.mo2_hub import npc_workflow as workflow


class Host:
    def __init__(self,root):
        self.root=root; self.active=True; self.fail=False; self.callbacks=[]; self.stale=False
        self.target=root/'mods'/'NPC output'; self.target.mkdir(parents=True)
        self.profile=root/'profile'; self.profile.mkdir()
        self.file=self.target/workflow.OUTPUT; self.file.write_bytes(b'original plugin')
        self.hashes={workflow.OUTPUT:digest(self.file)}
        self.saved=dict(selected={'Lydia':'A.esp'},hashes=self.hashes)
        workflow.path_for(self).write_text(json.dumps(self.saved),encoding='utf-8')
    def modsPath(self): return str(self.root/'mods')
    def profilePath(self): return str(self.profile)
    def modList(self): return self
    def pluginList(self): return self
    def pluginNames(self): return [workflow.OUTPUT]
    def state(self,name): return int(self.active)
    def loadOrder(self,name): return 0 if self.active else -1
    def setActive(self,name,value):
        if self.fail: return False
        self.active=value; return True
    def resolvePath(self,name): return str(self.target/name) if (self.active or self.stale) else ''
    def onNextRefresh(self,fn,unused): self.callbacks.append(fn)
    def refresh(self):
        callbacks,self.callbacks=self.callbacks,[]
        for fn in callbacks: fn()


class NpcResetTests(unittest.TestCase):
    def test_keep_saved_does_not_remove_actor_while_another_changes(self):
        saved={'Lydia':'A.esp','Serana':'B.esp'}
        choices=appearance_selection(saved,saved,'Lydia',KEEP_SAVED)
        choices=appearance_selection(choices,saved,'Serana','C.esp')
        self.assertEqual({'Lydia':'A.esp','Serana':'C.esp'},choices)
        self.assertEqual({},appearance_selection({'Lydia':'A.esp'},saved,'Lydia',''))

    def run_reset(self,host):
        result=[]
        modules={'mobase':SimpleNamespace(ModState=SimpleNamespace(ACTIVE=1)),
            'PyQt6':ModuleType('PyQt6'),'PyQt6.QtCore':SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda delay,fn:fn()))}
        with patch.dict('sys.modules',modules),patch.object(workflow,'own_name',return_value=host.target.name),patch.object(workflow,'read_manifest',return_value={'hashes':host.hashes}),patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            reset_choices(host,lambda target,error:result.append((target,error)))
        return result

    def test_last_override_is_disabled_preserved_and_recorded_as_reset(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); result=self.run_reset(host)
            self.assertEqual([(host.target,None)],result)
            self.assertFalse(host.active); self.assertEqual(b'original plugin',host.file.read_bytes())
            saved=workflow.load(host)
            self.assertEqual({},saved['selected']); self.assertEqual('reset',saved['status'])
            self.assertEqual(host.saved,json.loads(Path(saved['previous_choices']).read_text(encoding='utf-8')))

    def test_failed_disable_does_not_clear_saved_selection(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); host.fail=True
            result=self.run_reset(host)
            self.assertTrue(result[0][1]); self.assertTrue(host.active)
            self.assertEqual(host.saved,workflow.load(host))

    def test_ineffective_reset_restores_activation_and_retains_selection(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); host.stale=True
            result=self.run_reset(host)
            self.assertTrue(result[0][1]); self.assertTrue(host.active)
            self.assertEqual(host.saved,workflow.load(host))

    def test_changed_output_is_not_disabled(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); host.file.write_bytes(b'outside edit')
            with self.assertRaisesRegex(ValueError,'changed'): self.run_reset(host)
            self.assertTrue(host.active); self.assertEqual(host.saved,workflow.load(host))

    def test_active_dependent_prevents_removing_required_patch(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); host.pluginNames=lambda:[workflow.OUTPUT,'Dependent.esp']
            with patch.object(workflow,'plugin_masters',return_value=[workflow.OUTPUT]):
                with self.assertRaisesRegex(ValueError,'Dependent.esp requires'): self.run_reset(host)
            self.assertTrue(host.active); self.assertEqual(host.saved,workflow.load(host))

    def test_empty_pending_request_is_cleared_on_success(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); pending=workflow.path_for(host,'pending')
            pending.write_text('{"selected":{}}',encoding='utf-8')
            self.assertIsNone(self.run_reset(host)[0][1]); self.assertFalse(pending.exists())

    def test_reset_state_does_not_trigger_an_empty_automatic_rebuild(self):
        with TemporaryDirectory() as folder:
            host=Host(Path(folder)); self.run_reset(host)
            modules={'mobase':SimpleNamespace(ModState=SimpleNamespace(ACTIVE=1))}
            with patch.dict('sys.modules',modules),patch.object(workflow,'own_name',return_value=host.target.name):
                self.assertTrue(workflow.saved_build_current(host,workflow.load(host)))
                host.active=True
                with self.assertRaisesRegex(ValueError,'outside'): workflow.saved_build_current(host,workflow.load(host))
