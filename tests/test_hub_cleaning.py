import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from modlab.resources.mo2_hub.cleaning import reusable_job, inspect_cleaned_output, TOOL
from modlab.resources.mo2_hub.outputs import publish_output, digest, output_name, MANIFEST
from modlab.resources.mo2_hub.xedit import stage_plugins, write_record


class CleaningTests(unittest.TestCase):
    def test_reactivation_keeps_priority_when_cleaned_files_already_win(self):
        self.check_activation_priority(shadowed=False)

    def test_reactivation_moves_priority_only_when_a_file_is_shadowed(self):
        self.check_activation_priority(shadowed=True)

    def check_activation_priority(self, *, shadowed):
        from unittest.mock import Mock, patch
        from modlab.resources.mo2_hub import cleaning
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'Cleaned'; target.mkdir()
            (target / 'A.esp').write_bytes(b'checked copy')
            original = root / 'A.esp'; original.write_bytes(b'original')
            state = {'active': False, 'moved': False}
            def enable(name, active):
                state['active'] = active
                return True
            def move(name, priority):
                state['moved'] = True
            mods = SimpleNamespace(setActive=Mock(side_effect=enable), setPriority=Mock(side_effect=move),
                priority=lambda name: 3 if name == 'Other output' else 2,
                allMods=lambda: [target.name, 'Other output'])
            host = SimpleNamespace(profilePath=lambda: str(root / 'profile'), modList=lambda: mods,
                resolvePath=lambda name: str(target / name if state['active'] and (not shadowed or state['moved']) else original))
            manifest = {'projects': {}, 'hashes': {'A.esp': digest(target / 'A.esp')}}
            with patch.object(cleaning, 'read_manifest', return_value=manifest):
                finding = cleaning.activate_output(host, target)
            self.assertEqual('cleaning-applied', finding.code)
            self.assertEqual(int(shadowed), mods.setPriority.call_count)
            self.assertTrue(state['active'])

    def test_recheck_retains_identical_collection_but_records_changed_provenance(self):
        import sys
        from unittest.mock import patch
        from modlab.resources.mo2_hub import cleaning
        from modlab.resources.mo2_hub.cleaning_policy import CleaningDecision
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=root/'mods/Cleaned'; target.mkdir(parents=True)
            job=root/'builds/xedit/original'; (job/'Data').mkdir(parents=True)
            (job/'Data/B.esp').write_bytes(b'checked plugin')
            (job/'operation.json').write_text('{}')
            host=SimpleNamespace(modsPath=lambda:str(root/'mods'),profilePath=lambda:'Profile',resolvePath=lambda name:'unused')
            setup=SimpleNamespace(plugins=[SimpleNamespace(name='B.esp',origin='Source',load_order=0)])
            providers={'b.esp':['Source']}
            with patch.dict(sys.modules,{'mobase':SimpleNamespace()}), \
                 patch.object(cleaning,'output_target',return_value=target), \
                 patch.object(cleaning,'cleaning_decisions',return_value=[CleaningDecision('B.esp','clean','Reviewed fixture')]), \
                 patch.object(cleaning,'dependency_order',return_value=('B.esp',)), \
                 patch.object(cleaning,'known_record_issue',return_value=None), \
                 patch.object(cleaning,'reusable_job',side_effect=lambda path,*args:path==job), \
                 patch.object(cleaning,'source_providers',return_value=providers):
                cleaning.prepare_cleaning(host,setup,{},lambda path:'')
                before=(target/'B.esp').stat().st_mtime_ns
                manifest=(target/MANIFEST).read_bytes()
                collections=list(job.parent.glob('collection-*'))
                _,_,steps=cleaning.prepare_cleaning(host,setup,{},lambda path:'')
                self.assertEqual(before,(target/'B.esp').stat().st_mtime_ns)
                self.assertEqual(manifest,(target/MANIFEST).read_bytes())
                self.assertEqual(collections,list(job.parent.glob('collection-*')))
                self.assertTrue(any('retained' in step.lower() for step in steps))
                providers['b.esp'].append('New overlapping provider')
                cleaning.prepare_cleaning(host,setup,{},lambda path:'')
                self.assertEqual(len(collections)+1,len(list(job.parent.glob('collection-*'))))
                self.assertNotEqual(manifest,(target/MANIFEST).read_bytes())

    def setUp(self):
        from unittest.mock import patch
        guard = patch('modlab.resources.mo2_hub.skse.require_game_closed')
        guard.start(); self.addCleanup(guard.stop)

    def test_active_cleaned_copy_detects_source_updates_and_new_shadowed_providers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); profile = str(root / 'profiles/Test')
            target = root / 'mods' / output_name('Test', profile, TOOL); target.mkdir(parents=True)
            source = root / 'original.esp'; source.write_bytes(b'original')
            (target / 'A.esp').write_bytes(b'cleaned')
            job = root / 'builds/xedit/job'; job.mkdir(parents=True)
            write_record(job, {'sources': {'A.esp': {'path': str(source), 'sha256': digest(source)}}})
            manifest = {'owner': 'ModLab ' + TOOL, 'profile_path': profile, 'hashes': {'A.esp': digest(target / 'A.esp')},
                        'projects': {'A.esp': {'source_mod': 'Original', 'cleaning_job': str(job), 'source_providers': {'a.esp': ['Original']}}}}
            (target / MANIFEST).write_text(json.dumps(manifest))
            origins = [target.name, 'Original']
            organizer = SimpleNamespace(modsPath=lambda: str(root / 'mods'), profilePath=lambda: profile,
                profile=lambda: SimpleNamespace(name=lambda: 'Test'), resolvePath=lambda name: str(target / name),
                findFileInfos=lambda directory, predicate: [SimpleNamespace(filePath='A.esp', origins=origins)])
            self.assertEqual(('a.esp',), inspect_cleaned_output(organizer)[0])
            source.write_bytes(b'updated source')
            self.assertEqual('Blocked', inspect_cleaned_output(organizer)[1][0].level)
            source.write_bytes(b'original')
            origins.insert(1, 'New replacement')
            self.assertEqual('Blocked', inspect_cleaned_output(organizer)[1][0].level)

    def test_cached_cleaning_requires_current_source_master_and_unchanged_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'source'; source.mkdir()
            for name in ('A.esm', 'B.esp'):
                (source / name).write_bytes(b'TES4' + bytes(20))
            job = stage_plugins(root / 'job', ('A.esm', 'B.esp'), lambda name: source / name)
            (job / 'Data/B.esp').write_bytes(b'TES4' + bytes(20) + b'cleaned')
            record = json.loads((job / 'operation.json').read_text())
            record.update(mode='clean', result={'changed': True, 'sha256': digest(job / 'Data/B.esp')})
            write_record(job, record)
            (job / 'tool.log').write_text('Quick Clean mode finished.')
            (job / 'check.log').write_text('Done: Checking for Errors, Processed Records: 1, Errors found: 0\n--= All Done =--')
            resolve = lambda name: source / name
            self.assertTrue(reusable_job(job, ('A.esm', 'B.esp'), resolve))
            (source / 'A.esm').write_bytes(b'TES4' + bytes(21))
            self.assertFalse(reusable_job(job, ('A.esm', 'B.esp'), resolve))
            (source / 'A.esm').write_bytes(b'TES4' + bytes(20))
            (job / 'Data/B.esp').write_bytes(b'TES4' + bytes(22))
            self.assertFalse(reusable_job(job, ('A.esm', 'B.esp'), resolve))

    def test_new_cleaned_collection_drops_old_plugins_but_retains_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'mods/Cleaned'; target.mkdir(parents=True)
            for index, plugin in enumerate(('Old.esp', 'New.esp')):
                job = root / 'builds' / str(index); (job / 'output').mkdir(parents=True)
                (job / 'output' / plugin).write_bytes(b'TES4' + bytes(20))
                manifest = publish_output(target, job, 'Profile',
                    {plugin: {'outputs': [plugin], 'preset': 'xEdit Quick Auto Clean', 'source_mod': 'Source'}},
                    {plugin: digest(job / 'output' / plugin)}, tool=TOOL, replace_all=True)
            self.assertFalse((target / 'Old.esp').exists())
            self.assertTrue((target / 'New.esp').exists())
            self.assertTrue((Path(manifest['previous_output']) / 'Old.esp').exists())
