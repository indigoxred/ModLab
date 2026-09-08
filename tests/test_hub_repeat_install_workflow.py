import importlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as Obj
import unittest
from unittest.mock import patch

from modlab.resources.mo2_hub.installation import InstallResult
from tests import test_hub_repeat_install as fixtures


class RepeatInstallWorkflowTests(unittest.TestCase):
    def exercise(self, choice, *, edit_during_question=False, active=True, fail_record=False, cancel_early=False):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            archive,mods,target,history,previous,record=fixtures.RepeatInstallTests().fixture(root)
            events=[];results=[];native_calls=[];activations=[];signals=[]
            original=(target/'selected.esp').read_bytes()
            class Message:
                ButtonRole=Obj(AcceptRole=1,ActionRole=2)
                StandardButton=Obj(Cancel=3)
                def __init__(self,*args):self.buttons=[]
                def setWindowTitle(self,*args):pass
                def setText(self,*args):pass
                def setInformativeText(self,*args):pass
                def addButton(self,*args):
                    item=object();self.buttons.append(item);return item
                def setDefaultButton(self,*args):pass
                def exec(self):
                    if edit_during_question:(target/'selected.esp').write_bytes(b'user edit')
                def clickedButton(self):return self.buttons[{'keep':0,'change':1,'cancel':2}[choice]]
            qt=ModuleType('PyQt6'); core=ModuleType('PyQt6.QtCore'); widgets=ModuleType('PyQt6.QtWidgets')
            core.QTimer=Obj(singleShot=lambda delay,fn:events.append(fn));widgets.QMessageBox=Message
            mobase=ModuleType('mobase');mobase.ModState=Obj(ACTIVE=1)
            native=ModuleType('modlab.resources.mo2_hub.native_install')
            def install(*args,**kwargs):
                native_calls.append(args)
                return InstallResult('Cancelled','Example','',0,'Native installer reached'),None
            def write(path,data):
                if fail_record:raise OSError('Cannot save receipt')
                path.write_text(json.dumps(data))
            native.install_archive=install;native.write_record=write
            state=[active]
            def enable(name,enabled):state[0]=enabled;activations.append(name);return True
            mod=Obj(absolutePath=lambda:str(target))
            organizer=Obj(profilePath=lambda:'A',profile=lambda:Obj(name=lambda:'Profile A'),modsPath=lambda:str(mods),
                getPluginDataPath=lambda:str(root),
                modList=lambda:Obj(getMod=lambda name:mod,state=lambda name:int(state[0]),setActive=enable))
            records=root/'modlab/installations';records.mkdir(parents=True)
            previous.rename(records/previous.name)
            parent=Obj(finished=Obj(connect=signals.append,disconnect=signals.remove),hide=lambda:None,show=lambda:None)
            name='modlab.resources.mo2_hub.repeat_install_workflow'
            with patch.dict(sys.modules,{'PyQt6':qt,'PyQt6.QtCore':core,'PyQt6.QtWidgets':widgets,
                    'mobase':mobase,'modlab.resources.mo2_hub.native_install':native}), \
                 patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                sys.modules.pop(name,None)
                workflow=importlib.import_module(name)
                try:
                    initial,path=workflow.install_archive(organizer,archive,lambda result,path:results.append((result,path)),parent=parent)
                    self.assertEqual(initial.status,workflow.PENDING)
                    self.assertFalse(results)
                    if cancel_early:signals[0]()
                    ticks=0
                    while events:
                        ticks+=1;self.assertLess(ticks,150)
                        events.pop(0)()
                    self.assertEqual(len(results),1)
                    self.assertFalse(signals,'Temporary parent callbacks must be disconnected')
                    result,path=results[0]
                    saved=json.loads(path.read_text()) if path else None
                    return result,saved,native_calls,activations,(target/'selected.esp').read_bytes(),original
                finally:sys.modules.pop(name,None)

    def test_keep_reuses_exact_choices_without_reinstalling_or_changing_files(self):
        result,record,native,active,data,original=self.exercise('keep')
        self.assertEqual(result.status,'Installed, enabled')
        self.assertIn('reused_installation_record',record)
        self.assertFalse(native);self.assertFalse(active);self.assertEqual(data,original)

    def test_change_reaches_native_installer_once(self):
        result,record,native,active,data,original=self.exercise('change')
        self.assertEqual(len(native),1)
        self.assertFalse(active);self.assertEqual(data,original)

    def test_cancel_preserves_files_and_does_not_complete_installation(self):
        result,record,native,active,data,original=self.exercise('cancel')
        self.assertEqual(result.status,'Cancelled')
        self.assertIsNone(record);self.assertFalse(native);self.assertFalse(active);self.assertEqual(data,original)

    def test_edit_while_question_open_is_kept_and_blocks_reuse(self):
        result,record,native,active,data,original=self.exercise('keep',edit_during_question=True)
        self.assertEqual(result.status,'Installation needs attention')
        self.assertEqual(data,b'user edit');self.assertFalse(native);self.assertFalse(active)

    def test_disabled_install_is_enabled_only_after_explicit_keep(self):
        result,record,native,active,data,original=self.exercise('keep',active=False)
        self.assertEqual(result.status,'Installed, enabled')
        self.assertEqual(active,['Saved choices'])

    def test_receipt_failure_and_closed_parent_prevent_activation(self):
        for options in (dict(fail_record=True),dict(cancel_early=True)):
            with self.subTest(options=options):
                result,record,native,active,data,original=self.exercise('keep',active=False,**options)
                self.assertEqual(result.status,'Installation needs attention')
                self.assertFalse(active);self.assertFalse(native);self.assertEqual(data,original)
