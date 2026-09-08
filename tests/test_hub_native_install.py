import importlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj, ModuleType
import unittest
from unittest.mock import patch


class NativeInstallationVerificationTests(unittest.TestCase):
    def run_install(self, root, *, change_profile=False, change_archive=False, fail_record=False, fail_pending=False):
        mods=root/'mods'; mods.mkdir(); target=mods/'Example'; target.mkdir()
        (target/'Example.esp').write_bytes(b'plugin')
        archive=root/'Example.zip'; archive.write_bytes(b'archive')
        events=[]; refresh=[]; activated=[]; results=[]; messages=[]
        qt=ModuleType('PyQt6'); core=ModuleType('PyQt6.QtCore'); widgets=ModuleType('PyQt6.QtWidgets')
        core.QObject=object
        core.QEvent=Obj(Type=Obj(Show=1))
        core.QCoreApplication=Obj(applicationFilePath=lambda:'MO2.exe')
        core.QTimer=Obj(singleShot=lambda delay, callback:events.append(callback))
        app=Obj(installEventFilter=lambda value:None, removeEventFilter=lambda value:None)
        widgets.QApplication=Obj(instance=lambda:app)
        widgets.QCheckBox=type('QCheckBox',(),{}); widgets.QDialog=type('QDialog',(),{})
        mobase=ModuleType('mobase'); mobase.getFileVersion=lambda path:'2.5.2'
        mod=Obj(absolutePath=lambda:str(target), name=lambda:'Example')
        profile=['Profile A']
        def install(*args):
            if change_archive: archive.write_bytes(b'replaced archive')
            return mod
        organizer=Obj(managedGame=lambda:Obj(gameName=lambda:'Skyrim Special Edition'),
            profile=lambda:Obj(name=lambda:profile[0]), profilePath=lambda:profile[0],
            modsPath=lambda:str(mods), getPluginDataPath=lambda:str(root/'records'),
            installMod=install, onNextRefresh=refresh.append,
            modList=lambda:Obj(setActive=lambda name,active:activated.append(name) or True))
        name='modlab.resources.mo2_hub.native_install'
        with patch.dict(sys.modules, {'PyQt6':qt,'PyQt6.QtCore':core,'PyQt6.QtWidgets':widgets,'mobase':mobase}), \
             patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            sys.modules.pop(name,None)
            native=importlib.import_module(name)
            try:
                if fail_pending:
                    original=native.write_record
                    writes=[0]
                    def fail_second(*args):
                        writes[0]+=1
                        if writes[0]==2:raise OSError('Pending record not saved')
                        return original(*args)
                    native.write_record=fail_second
                    with self.assertRaises(OSError):
                        native.install_archive(organizer,archive,lambda result,path:results.append(result))
                    for callback in refresh:callback()
                    while events:events.pop(0)()
                    self.assertFalse(results,'Failed synchronous installation must not later report success')
                    self.assertFalse(activated)
                    return
                result, record=native.install_archive(organizer,archive,
                    lambda result,path:results.append(result),on_progress=messages.append)
                self.assertEqual(result.status,'Installed; activation pending')
                self.assertFalse(activated)
                self.assertFalse(results)
                if change_profile: profile[0]='Profile B'
                if fail_record:
                    def unavailable(*args):
                        raise OSError('Record storage unavailable')
                    native.write_record=unavailable
                for callback in refresh:callback()
                ticks=0
                while events:
                    ticks+=1
                    self.assertLess(ticks,100,'Verification must terminate, not wait indefinitely')
                    events.pop(0)()
                return results, activated, json.loads(record.read_text()), messages
            finally:
                sys.modules.pop(name,None)

    def test_completion_waits_for_inventory_and_retains_exact_hashes(self):
        with TemporaryDirectory() as tmp:
            results, active, record, messages=self.run_install(Path(tmp))
            self.assertEqual([r.status for r in results],['Installed, enabled'])
            self.assertEqual(active,['Example'])
            self.assertEqual(record['file_verification']['files']['Example.esp']['size'],6)
            self.assertEqual(len(record['file_verification']['archive_sha256']),64)
            self.assertTrue(messages)
            self.assertEqual(record['profile_path'],'Profile A')

    def test_archive_change_stops_activation_and_returns_recoverable_failure(self):
        with TemporaryDirectory() as tmp:
            results, active, record, messages=self.run_install(Path(tmp),change_archive=True)
            self.assertEqual([r.status for r in results],['Installation verification needs attention'])
            self.assertFalse(active)
            self.assertNotIn('file_verification',record)
            self.assertIn('archive changed',results[0].detail)
            self.assertTrue((Path(tmp)/'mods/Example/Example.esp').is_file())

    def test_profile_change_during_refresh_never_enables_in_another_profile(self):
        with TemporaryDirectory() as tmp:
            results, active, record, messages=self.run_install(Path(tmp),change_profile=True)
            self.assertEqual([r.status for r in results],['Installation verification needs attention'])
            self.assertFalse(active)
            self.assertNotIn('file_verification',record)

    def test_unsaved_verification_does_not_enable_or_report_queue_success(self):
        with TemporaryDirectory() as tmp:
            results, active, record, messages=self.run_install(Path(tmp),fail_record=True)
            self.assertEqual([r.status for r in results],['Installation record needs attention'])
            self.assertFalse(active)
            self.assertNotIn('file_verification',record)

    def test_failed_pending_record_does_not_leave_a_live_activation_callback(self):
        with TemporaryDirectory() as tmp:
            self.run_install(Path(tmp),fail_pending=True)
