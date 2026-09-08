import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
from modlab.resources.mo2_hub import shape_settings
from modlab.resources.mo2_hub.outputs import digest


class SettingsHost:
    def __init__(self, root, *, enable=True, switch_profile=False, fail_initial_refresh=False):
        self.root=root; self.active=False; self.enable=enable; self.switch_profile=switch_profile
        self.callbacks=[]; self.generated=None; self.other_profile=False; self.refresh_count=0
        self.fail_initial_refresh=fail_initial_refresh
        self.source=root/'mods/Source/SKSE/Plugins/skee64.ini'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'[Features]\r\nbEnableBodyMorph=0 ; original\r\nbEnableBodyGen=1\r\n')
    def modsPath(self): return str(self.root/'mods')
    def profilePath(self): return str(self.root/'profiles'/('Other' if self.other_profile else 'Test'))
    def profile(self): return SimpleNamespace(name=lambda:'Test')
    def resolvePath(self, relative): return str(self.generated/relative if self.active else self.source)
    def createMod(self, name):
        self.generated=self.root/'mods'/name; self.generated.mkdir()
        return SimpleNamespace(absolutePath=lambda:str(self.generated))
    def modList(self): return self
    def allMods(self): return ['Source', self.generated.name]
    def priority(self, name): return 0
    def setPriority(self, name, priority): pass
    def setActive(self, name, active):
        if self.enable: self.active=active
        return self.enable
    def onNextRefresh(self, callback, unused): self.callbacks.append(callback)
    def refresh(self):
        self.refresh_count+=1
        if self.fail_initial_refresh and self.refresh_count==1:
            raise RuntimeError('refresh failed')
        if self.switch_profile: self.other_profile=True
        callbacks,self.callbacks=self.callbacks,[]
        for callback in callbacks: callback()


class ShapeSettingsTests(unittest.TestCase):
    def test_controller_choice_changes_only_the_features_value(self):
        original='\ufeff[Features]\r\nbEnableBodyMorph=1 ; shapes\r\n  bEnableBodyGen = 1 ; my note\r\n[General]\r\nbEnableBodyGen=1\r\n'
        expected='\ufeff[Features]\r\nbEnableBodyMorph=1 ; shapes\r\n  bEnableBodyGen = 0 ; my note\r\n[General]\r\nbEnableBodyGen=1\r\n'
        self.assertEqual(expected, shape_settings.prepare_settings(original, 'use-obody'))
        self.assertEqual(original, shape_settings.prepare_settings(original, 'enable-morphs'))

    def test_enable_shapes_does_not_change_distribution_choice(self):
        self.assertEqual('[Features]\nbEnableBodyMorph=1\nbEnableBodyGen=1\n',
            shape_settings.prepare_settings('[Features]\nbEnableBodyMorph=0\nbEnableBodyGen=1\n', 'enable-morphs'))

    def test_incomplete_or_ambiguous_settings_are_not_rewritten(self):
        for text in ('[General]\nbEnableBodyGen=1\n',
                     '[Features]\nbEnableBodyGen=1\nbEnableBodyGen=0\n',
                     '[Features]\nbEnableBodyGen=maybe\n',
                     '[Features]\nbEnableBodyGen=1\n[FEATURES]\nbEnableBodyGen=0\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                shape_settings.prepare_settings(text, 'use-obody')
        with self.assertRaises(ValueError):
            shape_settings.prepare_settings('[Features]\nbEnableBodyGen=1\n', 'arbitrary-setting')

    def apply(self, host, action='use-obody'):
        results=[]
        source=Path(host.resolvePath(shape_settings.RELATIVE))
        modules={'mobase':SimpleNamespace(GuessedString=str), 'PyQt6':ModuleType('PyQt6'),
                 'PyQt6.QtCore':SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda delay, fn:fn()))}
        with patch.dict('sys.modules', modules), patch('modlab.resources.mo2_hub.skse.require_game_closed'):
            record=shape_settings.apply_settings(host, action, str(source), digest(source), lambda r,e:results.append((r,e)))
        return record,results

    def test_publication_keeps_original_backup_and_checks_effective_output(self):
        with TemporaryDirectory() as folder:
            host=SettingsHost(Path(folder)); original=host.source.read_bytes()
            record,results=self.apply(host)
            self.assertEqual([(record,None)], results)
            self.assertEqual('Settings applied and effective', json.loads(record.read_text())['status'])
            self.assertEqual(original, host.source.read_bytes())
            self.assertEqual(original, (record.parent/'source-settings.ini').read_bytes())
            self.assertEqual(original.replace(b'bEnableBodyGen=1', b'bEnableBodyGen=0'),
                Path(host.resolvePath(shape_settings.RELATIVE)).read_bytes())

    def test_failed_enable_or_profile_switch_never_reports_applied(self):
        for options in ({'enable':False}, {'switch_profile':True}):
            with self.subTest(options=options), TemporaryDirectory() as folder:
                host=SettingsHost(Path(folder), **options); original=host.source.read_bytes()
                record,results=self.apply(host)
                self.assertTrue(results[0][1]); self.assertFalse(host.active)
                self.assertEqual('Needs attention', json.loads(record.read_text())['status'])
                self.assertEqual(original, host.source.read_bytes())

    def test_manual_edit_to_managed_output_is_retained_instead_of_rebuilt(self):
        with TemporaryDirectory() as folder:
            host=SettingsHost(Path(folder)); self.apply(host)
            path=Path(host.resolvePath(shape_settings.RELATIVE))
            manual=path.read_bytes()+b'; manual edit\r\n'; path.write_bytes(manual)
            with self.assertRaisesRegex(ValueError, 'changed outside'):
                self.apply(host, 'enable-morphs')
            self.assertEqual(manual, path.read_bytes())

    def test_async_record_failure_still_finishes_once_with_an_error(self):
        with TemporaryDirectory() as folder:
            host=SettingsHost(Path(folder))
            original=Path.write_text
            def fail_record(path, *args, **kwargs):
                if path.name=='operation.json' and host.refresh_count:
                    raise PermissionError('record locked')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'write_text', new=fail_record):
                record,results=self.apply(host)
            self.assertEqual(1, len(results))
            self.assertIn('record locked', results[0][1])

    def test_queued_callback_cannot_activate_after_terminal_refresh_failure(self):
        with TemporaryDirectory() as folder:
            host=SettingsHost(Path(folder), fail_initial_refresh=True)
            record,results=self.apply(host)
            self.assertEqual(1, len(results)); self.assertIn('refresh failed', results[0][1])
            with patch('modlab.resources.mo2_hub.skse.require_game_closed'):
                host.refresh()
            self.assertFalse(host.active)
            self.assertEqual(1, len(results))


if __name__=='__main__': unittest.main()
