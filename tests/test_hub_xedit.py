"""Guard the actual cleaning boundary: selected plugin copies and verified results."""
import tempfile
import unittest
from pathlib import Path

from modlab.resources.mo2_hub.assessment import Plugin
from modlab.resources.mo2_hub.xedit import dependency_order, stage_plugins, stage_resources, verify_job, write_record, check_result, retain_notice_preferences


class XEditTests(unittest.TestCase):
    def test_record_check_keeps_earlier_non_master_injection_providers(self):
        from modlab.resources.mo2_hub.xedit import inspection_order
        plugins = [Plugin('Skyrim.esm', 0), Plugin('Injector.esp', 1, ('Skyrim.esm',)),
                   Plugin('Patch.esp', 2, ('Skyrim.esm',)), Plugin('Later.esp', 3), Plugin('Off.esp', -1)]
        self.assertEqual(('Skyrim.esm', 'Injector.esp', 'Patch.esp'), inspection_order(plugins, 'Patch.esp'))
        # Cleaning still only receives the selected plugin and its declared masters.
        self.assertEqual(('Skyrim.esm', 'Patch.esp'), dependency_order(plugins, 'Patch.esp'))

    def test_check_command_loads_context_but_clean_command_stays_isolated(self):
        from modlab.resources.mo2_hub.xedit_workflow import arguments
        names = ('Skyrim.esm', 'Injector.esp', 'Patch.esp')
        check = arguments(Path('job'), 'Patch.esp', 'check', Path('log'), context=names)
        clean = arguments(Path('job'), 'Patch.esp', 'clean', Path('log'), context=names)
        self.assertEqual(list(names), check[-3:])
        self.assertNotIn('Injector.esp', clean)
        self.assertEqual('Patch.esp', clean[-1])

    def test_dependencies_are_recursive_and_target_last(self):
        plugins = [Plugin('A.esm', 0), Plugin('B.esm', 1, ('a.esm',)),
                   Plugin('C.esp', 2, ('B.esm',))]
        self.assertEqual(('A.esm', 'B.esm', 'C.esp'), dependency_order(plugins, 'c.esp'))

    def test_missing_cyclic_and_unsafe_names_are_rejected(self):
        for plugins, target in [([Plugin('A.esp', 0, ('Missing.esm',))], 'A.esp'),
                                ([Plugin('A.esp', 0, ('B.esp',)), Plugin('B.esp', 1, ('A.esp',))], 'A.esp'),
                                ([Plugin('../A.esp', 0)], '../A.esp')]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                dependency_order(plugins, target)

    def job(self, root):
        source = root / 'source'; source.mkdir()
        for name in ('A.esm', 'B.esp'):
            (source / name).write_bytes(b'TES4' + bytes(20))
        job = stage_plugins(root / 'job', ('A.esm', 'B.esp'), lambda n: source / n)
        return job, source

    def test_cleaning_retains_originals_and_checks_all_masters(self):
        with tempfile.TemporaryDirectory() as temp:
            job, source = self.job(Path(temp))
            (job / 'Data/B.esp').write_bytes(b'TES4' + bytes(20) + b'changed')
            log = job / 'tool.log'; log.write_text('Quick Clean mode finished.')
            result = verify_job(job, 'clean', 0)
            self.assertTrue(result['changed'])
            self.assertEqual(b'TES4' + bytes(20), (source / 'B.esp').read_bytes())
            (job / 'Data/A.esm').write_bytes(b'TES4' + bytes(20) + b'changed')
            with self.assertRaisesRegex(ValueError, 'master'):
                verify_job(job, 'clean', 0)

    def test_success_exit_without_completion_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            job, _ = self.job(Path(temp))
            (job / 'tool.log').write_text('Loading...')
            with self.assertRaisesRegex(ValueError, 'completion'):
                verify_job(job, 'clean', 0)

    def test_error_check_exit_is_a_finding_and_cannot_change_files(self):
        with tempfile.TemporaryDirectory() as temp:
            job, _ = self.job(Path(temp))
            (job / 'tool.log').write_text('--= All Done =--')
            self.assertEqual(3, verify_job(job, 'check', 3)['record_errors'])
            (job / 'Data/B.esp').write_bytes(b'TES4' + bytes(24))
            with self.assertRaisesRegex(ValueError, 'inspection'):
                verify_job(job, 'check', 0)

    def test_original_change_invalidates_output(self):
        with tempfile.TemporaryDirectory() as temp:
            job, source = self.job(Path(temp))
            (job / 'tool.log').write_text('Quick Clean mode finished.')
            (source / 'B.esp').write_bytes(b'updated elsewhere')
            with self.assertRaisesRegex(ValueError, 'Source'):
                verify_job(job, 'clean', 0)

    def test_text_resources_are_available_and_changes_invalidate_cleaning(self):
        with tempfile.TemporaryDirectory() as temp:
            job, source = self.job(Path(temp))
            archive = source / 'A.bsa'; archive.write_bytes(b'archive resource')
            strings = source / 'A_english.strings'; strings.write_bytes(b'text table')
            stage_resources(job, {'A.bsa': archive, 'Strings/A_english.strings': strings}, 'ENGLISH')
            self.assertEqual(b'text table', (job / 'Data/Strings/A_english.strings').read_bytes())
            self.assertEqual(b'archive resource', (job / 'Data/A.bsa').read_bytes())
            (job / 'tool.log').write_text('Quick Clean mode finished.')
            verify_job(job, 'clean', 0)
            strings.write_bytes(b'changed text table')
            with self.assertRaisesRegex(ValueError, 'resource changed'):
                verify_job(job, 'clean', 0)

    def test_unresolved_localized_text_prevents_success(self):
        with tempfile.TemporaryDirectory() as temp:
            job, _ = self.job(Path(temp))
            (job / 'tool.log').write_text('Quick Clean mode finished. <Error: No strings file for lstring ID 0012>')
            with self.assertRaisesRegex(ValueError, 'text'):
                verify_job(job, 'clean', 0)

    def test_check_requires_completed_nonempty_target_and_matching_exit_status(self):
        good = 'Done: Checking for Errors, Processed Records: 17, Errors found: 2\n--= All Done =--'
        self.assertEqual(2, check_result(good, 2))
        for log, code in [('Starting...', 0), (good.replace('Records: 17', 'Records: 0'), 2),
                          (good, 0), ('lstring ID 12 could not be resolved\n' + good, 2)]:
            with self.subTest(log=log, code=code), self.assertRaises(ValueError):
                check_result(log, code)

    def test_completed_check_of_another_loaded_plugin_is_not_accepted(self):
        log = 'Checking for Errors in [02] Other.esp\nDone: Checking for Errors, Processed Records: 9, Errors found: 0\n--= All Done =--'
        with self.assertRaisesRegex(ValueError, 'requested plugin'):
            check_result(log, 0, target='Patch.esp')
        self.assertEqual(0, check_result(log.replace('Other.esp', 'Patch.esp'), 0, target='patch.esp'))

    def test_private_jobs_retain_acknowledged_notices_without_importing_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); previous = root / 'old'; previous.mkdir()
            (previous / 'plugins.sseviewsettings').write_text('[Init]\nFirst64Start=0\n[DeveloperMessage]\nVersion=4010506\nLastShownOn=46271\n[Paths]\nData=C:/RealGame/Data\n[Options]\nPatron=1\n')
            job = root / 'new'; job.mkdir()
            retain_notice_preferences(job, root / 'helper/xEdit.exe')
            content = (job / 'plugins.sseviewsettings').read_text()
            self.assertIn('First64Start = 0', content)
            self.assertIn('4010506', content)
            self.assertNotIn('RealGame', content)
            self.assertNotIn('Patron', content)
