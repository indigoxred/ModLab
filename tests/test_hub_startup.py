from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
import json


NOW = datetime(2026,9,6,12,tzinfo=timezone.utc).timestamp()
ROOT = r'C:\Games\Skyrim Special Edition'


def log_text(*, timestamp=NOW+1, runtime='01070680', status='loaded correctly', complete=True):
    filetime = int((timestamp + 11644473600) * 10000000)
    return (f'SKSE64 runtime: initialize (version = 2.3.1 {runtime} {filetime:016X}, os = test)\n'
        f'plugin directory = {ROOT}\\Data\\SKSE\\Plugins\\\n'
        'plugin msdia140.dll (00000000  00000000) no version data 0 (handle 0)\n'
        f'plugin Example.dll (00000001 Example 00000001) {status} (handle 1)\n' +
        ('init complete\n' if complete else ''))


class StartupTests(unittest.TestCase):
    def test_fsmp_menu_diagnostic_requires_exact_version_and_observed_load_sequence(self):
        from modlab.resources.mo2_hub.startup import integration_findings
        text = ('checking plugin hdtsmp64.dll\nchecking plugin SKSEMenuFramework.dll\n'
            'plugin hdtsmp64.dll (00000001 hdtsmp64 04010010) loaded correctly (handle 1)\n'
            'plugin SKSEMenuFramework.dll (00000001 SKSEMenuFramework 030E0000) loaded correctly (handle 2)\n'
            'init complete\n')
        findings = integration_findings(text)
        self.assertEqual(['fsmp-menu-registration'], [f.code for f in findings])
        self.assertEqual('Review', findings[0].level)
        self.assertIn('configs.json', findings[0].action)
        self.assertIn('physics', findings[0].explanation)
        for other in (text.replace('04010010', '04020000'),
                      text.replace('checking plugin hdtsmp64.dll\nchecking plugin SKSEMenuFramework.dll',
                                   'checking plugin SKSEMenuFramework.dll\nchecking plugin hdtsmp64.dll'),
                      text.replace('loaded correctly', 'failed to load'),
                      text.replace('init complete', 'initializing')):
            self.assertEqual((), integration_findings(other))

    def test_retained_receipt_is_invalidated_by_changed_dll_or_log(self):
        from modlab.resources.mo2_hub import startup
        from modlab.resources.mo2_hub.assessment import SetupSnapshot, Asset
        from tests.test_hub_native import dll_fixture
        with TemporaryDirectory() as tmp:
            root=Path(tmp); game=root/'game'; game.mkdir(); docs=root/'documents'; (docs/'SKSE').mkdir(parents=True)
            profile=str(root/'profile'); mods=root/'mods'; mods.mkdir()
            dll=mods/'Example.dll'; dll.write_bytes(dll_fixture())
            for name in ('skse64_loader.exe','skse64_1_7_104.dll'): (game/name).write_bytes(b'fixture')
            setup=SetupSnapshot('Skyrim Special Edition','1.7.104.0','Test',str(game),
                assets=(Asset('SKSE/Plugins/Example.dll',('Example mod',)),),skse_loader_present=True)
            host=Obj(profilePath=lambda:profile,modsPath=lambda:str(mods),resolvePath=lambda name:str(dll),
                managedGame=lambda:Obj(documentsDirectory=lambda:Obj(absolutePath=lambda:str(docs))))
            with patch('modlab.resources.mo2_hub.loot_workflow.context_signature',return_value=('context',)):
                request=startup.prepare_request(host,setup)
                request['started_at']=datetime.fromtimestamp(NOW,timezone.utc).isoformat()
                log=docs/'SKSE/skse64.log'; log.write_text(log_text().replace(ROOT,str(game)))
                record_path=root/'reports/launch/test/operation.json'; record_path.parent.mkdir(parents=True)
                record_path.write_text(json.dumps(dict(profile_path=profile,startup_request=request)))
                with patch.object(startup.time,'time',return_value=NOW+5):
                    self.assertTrue(startup.update_startup(host,record_path,setup))
                findings,checked=startup.inspect_startup(host,setup)
                self.assertTrue(checked);self.assertEqual('native-load-checked',findings[0].code)
                dll.write_bytes(dll_fixture()+b'changed')
                findings,checked=startup.inspect_startup(host,setup)
                self.assertFalse(checked);self.assertEqual('startup-outdated',findings[0].code)
                dll.write_bytes(dll_fixture())
                (record_path.parent/'skse64-observed.log').write_text('changed log')
                with self.assertRaisesRegex(ValueError,'changed'):
                    startup.inspect_startup(host,setup)

    def request(self):
        return dict(runtime='1.7.104.0', game_root=ROOT, started_at=datetime.fromtimestamp(NOW,timezone.utc).isoformat(),
            baseline_start=None, native=[dict(path='SKSE/Plugins/Example.dll', mod='Example mod', is_plugin=True),
                                       dict(path='SKSE/Plugins/msdia140.dll', mod='Support library', is_plugin=False)])

    def test_completed_load_distinguishes_plugin_from_support_library(self):
        from modlab.resources.mo2_hub.startup import evaluate_log
        result=evaluate_log(log_text(),self.request(),NOW+5)
        self.assertEqual(['Example.dll'],result['loaded'])
        self.assertEqual([],result['failed'])
        self.assertFalse(result['gameplay_verified'])

    def test_old_other_runtime_and_other_install_logs_are_not_accepted(self):
        from modlab.resources.mo2_hub.startup import evaluate_log
        for text in (log_text(timestamp=NOW-10),log_text(runtime='01064920'),
                     log_text().replace(ROOT,r'C:\Other Skyrim')):
            with self.assertRaises(ValueError): evaluate_log(text,self.request(),NOW+5)

    def test_reused_baseline_and_future_logs_are_not_accepted(self):
        from modlab.resources.mo2_hub.startup import evaluate_log, parse_log
        request=self.request();request['baseline_start']=parse_log(log_text())['started_at']
        with self.assertRaises(ValueError): evaluate_log(log_text(),request,NOW+5)
        with self.assertRaises(ValueError): evaluate_log(log_text(timestamp=NOW+99),self.request(),NOW+5)

    def test_silent_absence_and_loader_error_have_actionable_results(self):
        from modlab.resources.mo2_hub.startup import evaluate_log
        result=evaluate_log(log_text(status="couldn't load plugin 126"),self.request(),NOW+5)
        self.assertIn('126',result['failed'][0]['reason'])
        result=evaluate_log(log_text().replace('plugin Example.dll','unrecognized Example.dll'),self.request(),NOW+5)
        self.assertIn('no successful',result['failed'][0]['reason'])

    def test_incomplete_initialization_never_becomes_success(self):
        from modlab.resources.mo2_hub.startup import evaluate_log
        result=evaluate_log(log_text(complete=False),self.request(),NOW+5)
        self.assertEqual('Waiting for SKSE initialization',result['status'])

    def test_recorded_failure_wins_over_another_success_line(self):
        from modlab.resources.mo2_hub.startup import evaluate_log
        text=log_text() + 'plugin Example.dll (00000001 Example 00000001) reported as incompatible during load 0 (handle 1)\n'
        result=evaluate_log(text,self.request(),NOW+5)
        self.assertFalse(result['loaded']);self.assertEqual(1,len(result['failed']))
