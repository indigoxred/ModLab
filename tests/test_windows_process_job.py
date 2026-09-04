import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from modlab.validation import windows_integrity as integrity


@unittest.skipUnless(os.name == 'nt', 'requires native Windows jobs')
class ProcessJobTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='pj-'))
        integrity.set_low_integrity_tree(self.root)
        self.owners = []

    def tearDown(self):
        for owner in self.owners:
            try:
                self.wait(lambda: owner.observe().active_processes == 0)
                owner.close()
            except RuntimeError as error:
                if 'closed' not in str(error):
                    raise
        # Preserve native evidence and avoid deleting any unconfirmed live root.

    def wait(self, predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('native process condition timed out')

    def launch(self, code, **kwargs):
        def admit(launch):
            self.owners.append(launch.owner)
            callback = kwargs.get('before_resume')
            if callback:
                callback(launch)
        return integrity.launch_low_integrity_process(
            Path(sys.executable), ('-B', '-c', code), self.root,
            dict(os.environ, TEMP=str(self.root), TMP=str(self.root)),
            retain_owner=True, before_resume=admit)

    def test_retained_interface_exists(self):
        import inspect
        self.assertIn('retain_owner', inspect.signature(integrity.launch_low_integrity_process).parameters)

    def test_root_exit_does_not_release_surviving_child_and_grandchild(self):
        grand = "from pathlib import Path; import time; Path('grand').write_text('ready'); time.sleep(3)"
        child = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-B','-c',{grand!r}]); time.sleep(1)"
        root = f"import subprocess,sys; subprocess.Popen([sys.executable,'-B','-c',{child!r}])"
        launch = self.launch(root)
        owner = launch.owner
        handle = owner.process_handle
        self.wait(lambda: (self.root / 'grand').exists() and owner.observe().root_exit_code is not None)
        observed = owner.observe()
        self.assertEqual((launch.pid, launch.creation_time), (observed.pid, observed.creation_time))
        self.assertGreater(observed.active_processes, 0)
        self.assertGreaterEqual(observed.total_processes, 3)
        with self.assertRaisesRegex(RuntimeError, 'active'):
            owner.close()
        self.assertEqual(handle, owner.process_handle)
        self.wait(lambda: owner.observe().active_processes == 0)
        owner.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            owner.observe()

    def test_callback_precedes_first_instruction_and_uses_original_handle(self):
        marker = self.root / 'ran'
        def admit(launch):
            self.assertFalse(marker.exists())
            self.assertFalse(launch.owner.resumed)
            self.assertIsNone(launch.owner.observe().root_exit_code)
            self.assertEqual(integrity.IntegrityLevel.LOW, launch.integrity)
            with self.assertRaises(AttributeError):
                launch.owner.process_handle = 123
        with mock.patch.object(integrity._kernel32, 'OpenProcess', side_effect=AssertionError('must not reopen root')):
            launch = self.launch("from pathlib import Path; Path('ran').write_text('yes')", before_resume=admit)
            self.assertTrue(launch.owner.resumed)
            self.wait(lambda: launch.owner.observe().active_processes == 0)
        self.assertTrue(marker.exists())

    def test_breakaway_child_cannot_escape_private_job(self):
        code = """import subprocess,sys
from pathlib import Path
try:
    subprocess.Popen([sys.executable,'-B','-c',"from pathlib import Path; Path('escaped').write_text('bad')"], creationflags=0x01000000)
except OSError as error:
    Path('denied').write_text(str(error.winerror))
"""
        launch = self.launch(code)
        self.wait(lambda: launch.owner.observe().active_processes == 0)
        self.assertEqual('5', (self.root / 'denied').read_text())
        self.assertFalse((self.root / 'escaped').exists())

    def test_pre_resume_failures_never_execute_and_do_not_touch_unrelated_process(self):
        from modlab.validation import windows_process_job as jobs
        unrelated = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(5)'])
        try:
            for target, attribute in ((jobs, '_assign_and_verify'), (integrity, '_verify_exact_low_process')):
                with self.subTest(attribute=attribute), mock.patch.object(target, attribute, side_effect=OSError('injected admission failure')):
                    with self.assertRaisesRegex(OSError, 'injected admission failure'):
                        self.launch("from pathlib import Path; Path('ran').write_text('bad')")
                self.assertFalse((self.root / 'ran').exists())
                self.assertIsNone(unrelated.poll())
            with self.assertRaisesRegex(ValueError, 'callback failed'):
                self.launch("from pathlib import Path; Path('ran').write_text('bad')", before_resume=lambda launch: (_ for _ in ()).throw(ValueError('callback failed')))
            self.assertFalse((self.root / 'ran').exists())
        finally:
            unrelated.wait(timeout=10)

    def test_close_failures_retain_original_handle_and_can_retry(self):
        from modlab.validation import windows_process_job as jobs
        launch = self.launch('pass')
        owner = launch.owner
        self.wait(lambda: owner.observe().active_processes == 0)
        handle = owner.process_handle
        with mock.patch.object(jobs, '_close_native_handle', side_effect=OSError('injected close failure')):
            with self.assertRaisesRegex(OSError, 'injected close failure'):
                owner.close()
        self.assertEqual(handle, owner.process_handle)
        self.assertEqual(0, owner.observe().active_processes)
        owner.close()
        owner.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            _ = owner.process_handle

    def test_nonowner_controller_copy_and_second_transfer_are_rejected(self):
        launch = self.launch('pass')
        owner = launch.owner
        with mock.patch('os.getpid', return_value=-1):
            for action in (owner.observe, owner.close, lambda: owner.process_handle, lambda: owner.resumed):
                with self.assertRaisesRegex(RuntimeError, 'controller'):
                    action()
        with self.assertRaises(TypeError):
            copy.copy(owner)
        with self.assertRaises(TypeError):
            copy.deepcopy(owner)
        with self.assertRaisesRegex(RuntimeError, 'resumed'):
            owner._mark_resumed()

    def test_exited_259_is_not_running(self):
        launch = self.launch("import ctypes; ctypes.windll.kernel32.ExitProcess(259)")
        self.wait(lambda: launch.owner.observe().active_processes == 0)
        self.assertEqual(259, launch.owner.observe().root_exit_code)

    def test_invalid_callback_arguments_rejected_before_creation(self):
        for kwargs in ({'before_resume': lambda launch: None}, {'retain_owner': True, 'before_resume': 1}, {'retain_owner': 1}):
            with self.subTest(kwargs=kwargs), mock.patch.object(integrity._kernel32, 'CreateProcessW', side_effect=AssertionError('must not create')):
                with self.assertRaises((TypeError, ValueError)):
                    integrity.launch_low_integrity_process(Path(sys.executable), (), self.root, {}, **kwargs)

    def test_exact_low_rejects_other_rids_in_low_band(self):
        real_set = integrity._set_token_integrity
        with mock.patch.object(integrity, '_set_token_integrity', side_effect=lambda token, level: real_set(token, 0x1100)):
            with self.assertRaisesRegex(OSError, 'exact Low RID'):
                self.launch("from pathlib import Path; Path('ran').write_text('bad')")
        self.assertFalse((self.root / 'ran').exists())

    def test_native_membership_and_limit_verification_fail_before_execution(self):
        from modlab.validation import windows_process_job as jobs
        real_query = jobs._query
        for flag in (0x800, 0x1000, 0x2000):
            def query(job, kind, structure):
                result = real_query(job, kind, structure)
                if kind == 9:
                    result.BasicLimitInformation.LimitFlags = flag
                return result
            with self.subTest(flag=flag), mock.patch.object(jobs, '_query', side_effect=query):
                with self.assertRaisesRegex(OSError, 'limits or breakaway'):
                    self.launch("from pathlib import Path; Path('ran').write_text('bad')")
        with mock.patch.object(jobs._kernel32, 'IsProcessInJob', return_value=False):
            with self.assertRaisesRegex(OSError, 'membership'):
                self.launch("from pathlib import Path; Path('ran').write_text('bad')")
        with mock.patch.object(jobs._kernel32, 'GetProcessId', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'PID'):
                self.launch("from pathlib import Path; Path('ran').write_text('bad')")
        self.assertFalse((self.root / 'ran').exists())

    def test_failed_suspended_termination_exposes_original_owner_for_recovery(self):
        primary = ValueError('primary callback failure')
        with mock.patch.object(integrity, '_terminate_created_process', side_effect=OSError('termination failed')):
            with self.assertRaises(ValueError) as caught:
                self.launch("from pathlib import Path; Path('ran').write_text('bad')", before_resume=lambda launch: (_ for _ in ()).throw(primary))
        self.assertIs(primary, caught.exception)
        owner = caught.exception.owner
        try:
            self.assertFalse(owner.resumed)
            self.assertIsNone(owner.observe().root_exit_code)
            self.assertEqual(1, owner.observe().active_processes)
            with self.assertRaisesRegex(RuntimeError, 'active'):
                owner.close()
            self.assertFalse((self.root / 'ran').exists())
        finally:
            integrity._terminate_created_process(owner.process_handle)
            owner.close()

    def test_token_close_failure_remains_owned_and_prevents_resume(self):
        from modlab.validation import windows_process_job as jobs
        def fail_token(handle):
            raise OSError('token close failed')
        with mock.patch.object(jobs, '_close_native_handle', side_effect=fail_token):
            with self.assertRaisesRegex(OSError, 'token close failed') as caught:
                self.launch("from pathlib import Path; Path('ran').write_text('bad')")
        owner = caught.exception.owner
        self.assertTrue(owner._token)
        self.assertFalse((self.root / 'ran').exists())
        self.assertFalse(owner.resumed)
        owner.close()

    def test_partial_close_failure_preserves_process_handle_until_retry(self):
        from modlab.validation import windows_process_job as jobs
        launch = self.launch('pass')
        owner = launch.owner
        self.wait(lambda: owner.observe().active_processes == 0)
        handle = owner.process_handle
        real_close = jobs._close_native_handle
        def close(value):
            if value == handle:
                raise OSError('process close failed')
            return real_close(value)
        with mock.patch.object(jobs, '_close_native_handle', side_effect=close):
            with self.assertRaisesRegex(OSError, 'process close failed'):
                owner.close()
        self.assertEqual(handle, owner.process_handle)
        owner.close()

    def test_unknown_observations_raise_and_keep_original_ownership(self):
        from modlab.validation import windows_process_job as jobs
        owner = self.launch('import time; time.sleep(.4)').owner
        handle = owner.process_handle
        with mock.patch.object(jobs._kernel32, 'WaitForSingleObject', return_value=0xFFFFFFFF):
            with self.assertRaisesRegex(OSError, 'wait failed'):
                owner.observe()
        with mock.patch.object(jobs._kernel32, 'QueryInformationJobObject', return_value=False):
            with self.assertRaisesRegex(OSError, 'QueryInformation'):
                owner.observe()
        self.wait(lambda: owner.observe().active_processes == 0)
        with mock.patch.object(jobs._kernel32, 'GetExitCodeProcess', return_value=False):
            with self.assertRaisesRegex(OSError, 'GetExitCode'):
                owner.observe()
        self.assertEqual(handle, owner.process_handle)

    def test_ambiguous_resume_does_not_terminate_a_running_tree(self):
        real_resume = integrity._kernel32.ResumeThread
        def ambiguous(thread):
            self.assertEqual(1, real_resume(thread))
            return 0
        with mock.patch.object(integrity._kernel32, 'ResumeThread', side_effect=ambiguous):
            with self.assertRaisesRegex(OSError, 'suspend count') as caught:
                self.launch("import time; from pathlib import Path; time.sleep(.2); Path('survived').write_text('yes'); time.sleep(.2)")
        owner = caught.exception.owner
        self.assertFalse(owner.resumed)
        self.wait(lambda: (self.root / 'survived').exists())
        self.wait(lambda: owner.observe().active_processes == 0)

    def test_token_cleanup_does_not_replace_primary_admission_error(self):
        from modlab.validation import windows_process_job as jobs
        primary = ValueError('primary exact Low failure')
        with mock.patch.object(integrity, '_verify_exact_low_process', side_effect=primary), mock.patch.object(jobs, '_close_native_handle', side_effect=OSError('secondary token close failure')):
            with self.assertRaises(ValueError) as caught:
                self.launch('pass')
        self.assertIs(primary, caught.exception)
        self.assertTrue(caught.exception.owner._token)
        caught.exception.owner.close()

    def test_created_callback_receipts_pid_before_job_failure(self):
        from modlab.validation import windows_process_job as jobs
        created = []
        with mock.patch.object(jobs._kernel32, 'CreateJobObjectW', return_value=None):
            with self.assertRaisesRegex(OSError, 'CreateJobObject') as caught:
                integrity.launch_low_integrity_process(Path(sys.executable), ('-B', '-c', 'pass'), self.root, dict(os.environ), retain_owner=True, on_created=created.append)
        self.assertEqual([caught.exception.owner._pid], created)
        self.assertGreater(created[0], 0)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            caught.exception.owner.observe()

    def test_original_process_information_cannot_transfer_twice(self):
        from modlab.validation import windows_process_job as jobs
        real_take = jobs.ProcessJobOwner._take_created
        def take(information):
            owner = real_take(information)
            with self.assertRaisesRegex(RuntimeError, 'already transferred'):
                real_take(information)
            return owner
        with mock.patch.object(jobs.ProcessJobOwner, '_take_created', side_effect=take):
            self.launch('pass')


if __name__ == '__main__':
    unittest.main()
