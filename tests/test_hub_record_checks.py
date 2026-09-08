import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch
import hashlib

from modlab.resources.mo2_hub.assessment import Plugin
from modlab.resources.mo2_hub.record_checks import check_targets, inspect_targets, file_hash


class RecordChecks(unittest.TestCase):
    def test_loose_text_table_changes_and_removal_change_host_context(self):
        from modlab.resources.mo2_hub import record_checks as rc
        with TemporaryDirectory() as temp:
            root=Path(temp); helper=root/'helper.exe';helper.write_bytes(b'exe')
            ini=root/'Skyrim.ini';ini.write_bytes(b'ini'); strings=root/'Example_english.strings';strings.write_bytes(b'first')
            organizer=Obj(getPluginDataPath=lambda:str(root),findFiles=lambda folder,pats:[],
                          profile=lambda:Obj(absoluteIniFilePath=lambda name:str(ini)),profilePath=lambda:str(root))
            with patch('modlab.resources.mo2_hub.helpers.locate_helper',return_value=[helper]), \
                 patch('modlab.resources.mo2_hub.helpers.load_locations',return_value={}), \
                 patch('modlab.resources.mo2_hub.vfs.find_files',side_effect=lambda *a:[strings] if strings.exists() else []):
                before=rc.host_context(organizer)
                strings.write_bytes(b'changed')
                after=rc.host_context(organizer)
                self.assertNotEqual(before,after)
                strings.unlink()
                self.assertNotEqual(after,rc.host_context(organizer))

    def test_hash_read_does_not_mistake_access_timestamp_for_content_change(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'helper.exe'; path.write_bytes(b'helper')
            original = Path.stat
            calls = []
            def stat(value, *args, **kwargs):
                result = original(value, *args, **kwargs)
                calls.append(value)
                return Obj(st_size=result.st_size, st_mtime_ns=result.st_mtime_ns,
                           st_ctime_ns=result.st_ctime_ns, st_atime_ns=len(calls), st_mode=result.st_mode)
            with patch.object(Path, 'stat', stat):
                self.assertEqual(hashlib.sha256(b'helper').hexdigest(), file_hash(path))

    def test_recheck_reuses_results_but_changed_master_invalidates_dependents(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['Skyrim.esm', 'A.esp', 'B.esp']:
                (root/name).write_bytes(b'original')
            plugins = (Plugin('Skyrim.esm', 0), Plugin('A.esp', 1, ('Skyrim.esm',), 'Mod A'),
                       Plugin('B.esp', 2, (), 'Mod B'))
            calls=[]
            def run(name):
                calls.append(name)
                job=root/str(len(calls)); job.mkdir()
                (job/'tool.log').write_text(f'Checking for Errors in [01] {name}\nDone: Checking for Errors, Processed Records: 3, Errors found: 0\n--= All Done =--')
                return job, {'record_errors':0}, None, None
            cache=root/'checks.json'
            resolve=lambda name:root/name
            check_targets(plugins, resolve, {'helper':'one'}, cache, run)
            check_targets(plugins, resolve, {'helper':'one'}, cache, run)
            self.assertEqual(['A.esp','B.esp'],calls)
            (root/'Skyrim.esm').write_bytes(b'updated master')
            check_targets(plugins, resolve, {'helper':'one'}, cache, run)
            self.assertEqual(['A.esp','B.esp','A.esp','B.esp'],calls)
            findings=inspect_targets(plugins, resolve, {'helper':'one'}, cache)
            self.assertEqual(['record-checks-current'],[f.code for f in findings])

    def test_non_master_provider_change_invalidates_later_check_only(self):
        from modlab.resources.mo2_hub.record_checks import signature
        with TemporaryDirectory() as temp:
            root = Path(temp)
            plugins = [Plugin('A.esp', 0), Plugin('Injector.esp', 1), Plugin('Patch.esp', 2)]
            for p in plugins: (root/p.name).write_bytes(b'original')
            resolve = lambda n: root/n
            before = [signature(plugins, p.name, resolve, {}) for p in plugins]
            (root/'Injector.esp').write_bytes(b'changed injected records')
            after = [signature(plugins, p.name, resolve, {}) for p in plugins]
            self.assertEqual(before[0], after[0])
            self.assertNotEqual(before[2], after[2])

    def test_record_error_reports_mod_and_does_not_become_cleaning_permission(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); (root/'Bad.esp').write_bytes(b'plugin')
            plugins=(Plugin('Bad.esp',0,(), 'My follower'),)
            job=root/'job';job.mkdir()
            (job/'tool.log').write_text('Checking for Errors in [01] Bad.esp\n[00:01] TestFollower "Named follower" [NPC_:01001234]\n[00:01]     NPC: <Error: Could not be resolved>\nDone: Checking for Errors, Processed Records: 3, Errors found: 1\n--= All Done =--')
            findings,_=check_targets(plugins, lambda n:root/n, {}, root/'checks.json',
                lambda name:(job,{'record_errors':1},None,None))
            issue=next(f for f in findings if f.code=='record-errors')
            self.assertIn('My follower',issue.title)
            self.assertIn('Could not be resolved',issue.detail)
            self.assertIn('Named follower', issue.detail)
            self.assertIn('NPC_:01001234', issue.detail)
            self.assertNotEqual('Info',issue.level)
            self.assertIn('clean',issue.explanation.lower())

    def test_changed_or_wrong_target_log_cannot_support_cached_success(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); (root/'A.esp').write_bytes(b'plugin')
            plugins=(Plugin('A.esp',0),)
            job=root/'job';job.mkdir()
            (job/'tool.log').write_text('Checking for Errors in [01] Wrong.esp\nDone: Checking for Errors, Processed Records: 3, Errors found: 0\n--= All Done =--')
            findings,_=check_targets(plugins,lambda n:root/n,{},root/'checks.json',
                lambda name:(job,{'record_errors':0},None,None))
            self.assertTrue(any(f.code=='record-check-incomplete' for f in findings))

    def test_missing_master_does_not_start_helper(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); (root/'A.esp').write_bytes(b'plugin')
            plugins=(Plugin('A.esp',0,('Missing.esm',)),)
            calls=[]
            findings,_=check_targets(plugins,lambda n:root/n,{},root/'checks.json',lambda name:calls.append(name))
            self.assertEqual([],calls)
            self.assertTrue(any(f.code=='record-check-incomplete' for f in findings))
