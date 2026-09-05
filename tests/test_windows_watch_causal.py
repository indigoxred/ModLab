import os
from pathlib import Path
import tempfile
import unittest
from modlab.validation import windows_watch as watch
from modlab.validation.windows_vault_security import create_vault
from modlab.validation.mo2_containment_model import ContainmentScenario

@unittest.skipUnless(os.name == 'nt', 'native watcher')
class CausalWatchTests(unittest.TestCase):
    def test_watch_without_process_admission_cannot_complete(self):
        with tempfile.TemporaryDirectory(prefix='cw-') as directory:
            root = Path(directory)
            watched = root / 'watched'
            watched.mkdir()
            with create_vault(root / 'evidence') as vault:
                physical = watch.watch_root('SourceMods', watched)
                from dataclasses import replace
                request = watch.WatchRequest('watch-request:'+'a'*64, 'watch-session:'+'b'*64,
                    'containment-run:'+'c'*32, ContainmentScenario.MERGE_EXISTING,
                    vault.path, vault.path/'stop.token',
                    tuple(replace(physical, root_kind=kind) for kind in watch.ROOT_KINDS),
                    vault.path, vault.identity.volume_serial, vault.identity.file_id, vault.creator_sid)
                request_path = vault.path/'request.json'
                watch.start_watch(request)
                self.assertFalse(watch.stop_watch(request_path).complete,
                                 'watch-only stop cannot manufacture admitted process completion')

    def test_trusted_code_guard_rejects_low_file(self):
        from modlab.validation import windows_vault_security as security
        from modlab.validation.windows_integrity import set_low_integrity_tree
        self.assertTrue(hasattr(security, 'pin_trusted_paths'), 'trusted runtime guard is required')
        with tempfile.TemporaryDirectory(prefix='ct-') as directory:
            code = Path(directory) / 'worker.py'
            code.write_text('pass')
            set_low_integrity_tree(code.parent)
            with self.assertRaises(Exception):
                security.pin_trusted_paths((code,))


    def _case(self):
        from dataclasses import replace
        from modlab.validation.windows_integrity import set_low_integrity_tree
        root = Path(tempfile.mkdtemp(prefix='wc-'))
        watched = root / 'watched'
        watched.mkdir()
        low = root / 'low'
        low.mkdir()
        set_low_integrity_tree(low)
        vault = create_vault(root / 'vault')
        evidence = vault.path / 'watch'
        evidence.mkdir()
        physical = watch.watch_root('SourceMods', watched)
        request = watch.WatchRequest('watch-request:'+'a'*64, 'watch-session:'+'b'*64,
            'containment-run:'+'c'*32, ContainmentScenario.MERGE_EXISTING, evidence, evidence/'stop.token',
            tuple(replace(physical, root_kind=kind) for kind in watch.ROOT_KINDS),
            vault.path, vault.identity.volume_serial, vault.identity.file_id, vault.creator_sid)
        path = evidence/'request.json'
        pid = watch.start_watch(request)
        self.addCleanup(vault.close)
        self.addCleanup(lambda: watch.stop_watch(path, recovery=True))
        return request, path, pid, low

    def _launch(self, path, low, code='pass'):
        import sys, time
        from modlab.validation.windows_integrity import launch_low_integrity_process
        launch = launch_low_integrity_process(Path(sys.executable), ('-B','-c',code), low,
            dict(os.environ, TEMP=str(low), TMP=str(low)), retain_owner=True,
            before_resume=lambda value: watch.admit_watch_launch(path, value))
        deadline = time.monotonic()+15
        while launch.owner.observe().active_processes:
            if time.monotonic()>deadline:
                self.fail('disposable tree did not exit')
            time.sleep(.02)
        return launch

    def _read(self, request, pid):
        return watch.watch_receipt_from_files(request, pid, request.evidence_root/'ready.json',
            request.evidence_root/'events.ndjson', request.evidence_root/'terminal.json')

    def test_real_admitted_child_completes_and_reconstructs_from_durable_proof(self):
        from unittest import mock
        request, path, pid, low = self._case()
        launch = self._launch(path, low)
        watch.complete_watch_launch(path, launch.owner)
        receipt = watch.stop_watch(path)
        self.assertTrue(receipt.complete, receipt.error)
        with mock.patch.object(watch, '_exact_process_status', side_effect=AssertionError('durable proof must not reopen PIDs')):
            self.assertTrue(self._read(request,pid).complete)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            launch.owner.observe()

    def test_recovery_stop_never_completes_even_after_quiescence(self):
        request, path, pid, low = self._case()
        launch = self._launch(path, low)
        watch.complete_watch_launch(path)
        self.assertFalse(watch.stop_watch(path, recovery=True).complete)
        import json
        self.assertEqual('RecoveryCleanupStop', json.loads(request.stop_token_path.read_bytes())['kind'])
        self.assertFalse(self._read(request,pid).complete)

    def test_missing_quiescence_refuses_completed(self):
        request, path, pid, low = self._case()
        self._launch(path, low)
        self.assertFalse(watch.stop_watch(path).complete)

    def test_verified_resume_is_required_not_inferred_from_exit(self):
        from unittest import mock
        from modlab.validation.windows_process_job import ProcessJobOwner
        request, path, pid, low = self._case()
        self._launch(path, low)
        with mock.patch.object(ProcessJobOwner, 'resumed', new_callable=mock.PropertyMock, return_value=False):
            with self.assertRaisesRegex(watch.WatchProtocolError, 'resume'):
                watch.complete_watch_launch(path)

    def test_missing_exit_record_and_altered_raw_or_outcome_refuse_reconstruction(self):
        import json
        from unittest import mock
        for filename in ('launch-admission.json','process-quiescence.json','stop.token','worker-exit.json','events.ndjson','terminal.json','outcome.json'):
            with self.subTest(filename=filename):
                request,path,pid,low=self._case()
                self._launch(path,low)
                watch.complete_watch_launch(path)
                self.assertTrue(watch.stop_watch(path).complete)
                target=request.evidence_root/filename
                if filename=='worker-exit.json':
                    target.unlink()
                else:
                    target.write_bytes(target.read_bytes()+b' ')
                self.assertFalse(self._read(request,pid).complete)

    def test_low_early_stop_cannot_end_watcher_before_process_exit(self):
        request,path,pid,low=self._case()
        code = f"from pathlib import Path; import time; p=Path({str(request.stop_token_path)!r}); denied=False\ntry: p.write_bytes(b'stop\\n')\nexcept PermissionError: denied=True\nPath('denied').write_text(str(denied)); time.sleep(.1)"
        self._launch(path,low,code)
        self.assertEqual('True',(low/'denied').read_text())
        self.assertFalse(request.stop_token_path.exists())
        watch.complete_watch_launch(path)
        self.assertTrue(watch.stop_watch(path).complete)

    def test_normal_stop_terminal_binds_exact_identity_and_hash(self):
        import hashlib,json
        from modlab.platform.windows_exact_fs import identity_at_path
        request,path,pid,low=self._case()
        self._launch(path,low)
        watch.complete_watch_launch(path)
        self.assertTrue(watch.stop_watch(path).complete)
        identity=identity_at_path(request.stop_token_path)
        terminal=json.loads((request.evidence_root/'terminal.json').read_bytes())
        self.assertEqual({'sha256':hashlib.sha256(request.stop_token_path.read_bytes()).hexdigest(),
            'volumeSerial':identity.volume_serial,'fileId':identity.file_id},terminal['stopBinding'])

    def test_noncanonical_and_unknown_causal_fields_are_rejected(self):
        import json
        request,path,pid,low=self._case()
        self._launch(path,low)
        session=watch._local_launch_session(path)
        data=(request.evidence_root/'launch-admission.json').read_bytes()
        for candidate in (data+b' ', watch._canonical_bytes(dict(json.loads(data), extra=True))):
            with self.assertRaises(watch.WatchProtocolError):
                watch.causal_record_from_bytes(candidate,request,session.claim,session.launch)

    def test_request_parser_has_no_native_filesystem_side_effects(self):
        from unittest import mock
        request,path,pid,low=self._case()
        data=watch.watch_request_to_bytes(request)
        with mock.patch.object(Path,'exists',side_effect=AssertionError('pure parser')):
            self.assertEqual(request,watch.watch_request_from_bytes(data))


    def test_completed_attempt_reconstructs_after_actual_controller_exit(self):
        import json, subprocess, sys
        code = """import json
from tests.test_windows_watch_causal import CausalWatchTests
from modlab.validation import windows_watch as watch
case=CausalWatchTests()
request,path,pid,low=case._case()
case._launch(path,low)
watch.complete_watch_launch(path)
receipt=watch.stop_watch(path)
assert receipt.complete,receipt.error
case.doCleanups()
print(json.dumps([str(path),pid]))
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,check=True)
        path,pid=json.loads(result.stdout)
        request=watch._load_request_path(Path(path))
        receipt=self._read(request,pid)
        self.assertTrue(receipt.complete,receipt.error)

    def test_process_close_failure_retains_original_owner_for_incomplete_retry(self):
        from unittest import mock
        from modlab.validation import windows_process_job as jobs
        request,path,pid,low=self._case()
        launch=self._launch(path,low)
        watch.complete_watch_launch(path)
        process=launch.owner.process_handle
        real_close=jobs._close_native_handle
        def fail(handle):
            if handle==process:
                raise OSError('injected process close failure')
            return real_close(handle)
        with mock.patch.object(jobs,'_close_native_handle',side_effect=fail):
            with self.assertRaises(OSError) as raised:
                watch.stop_watch(path)
        self.assertIs(launch.owner,raised.exception.owner)
        self.assertIs(launch.owner,watch._local_launch_session(path).process_owner)
        self.assertFalse(watch.stop_watch(path).complete)
        self.assertNotIn(path.absolute(),watch._LOCAL_SESSIONS)

    def test_trusted_guard_close_failure_preserves_retryable_pin(self):
        from unittest import mock
        from modlab.validation.windows_vault_security import pin_trusted_paths
        from modlab.platform.windows_exact_fs import PinnedObject, ExactObjectOwnershipError
        root=Path(tempfile.mkdtemp(prefix='cg-'))
        code=root/'worker.py'
        code.write_text('pass')
        guard=pin_trusted_paths((code,))
        real_close=PinnedObject.close
        def fail(pin):
            if pin.path==code:
                raise OSError('injected runtime close failure')
            return real_close(pin)
        with mock.patch.object(PinnedObject,'close',new=fail):
            with self.assertRaises(ExactObjectOwnershipError) as raised:
                guard.close()
        self.assertEqual((code,),tuple(pin.path for pin in raised.exception.verification))
        raised.exception.resolve()
        guard.close()


    def test_stop_replacement_with_identical_bytes_fails_exact_identity_binding(self):
        request,path,pid,low=self._case()
        self._launch(path,low)
        watch.complete_watch_launch(path)
        self.assertTrue(watch.stop_watch(path).complete)
        data=request.stop_token_path.read_bytes()
        request.stop_token_path.rename(request.evidence_root/'old-stop')
        request.stop_token_path.write_bytes(data)
        self.assertFalse(self._read(request,pid).complete)


    def test_durable_read_preserves_simultaneous_terminal_and_stop_close_owners(self):
        from unittest import mock
        from modlab.platform.windows_exact_fs import PinnedObject, ExactObjectOwnershipError
        request,path,pid,low=self._case()
        self._launch(path,low)
        watch.complete_watch_launch(path)
        self.assertTrue(watch.stop_watch(path).complete)
        claim=watch._load_controller_claim(request)
        launch=watch._load_worker_launch(request)
        captured=watch._capture_worker_evidence(request,launch)
        real_read_close=watch._close_handle
        real_pin_close=PinnedObject.close
        def fail_read(handle,label):
            if label=='terminal record readback':
                return 'injected terminal close'
            return real_read_close(handle,label)
        def fail_stop(pin):
            if pin.path==request.stop_token_path:
                raise OSError('injected stop pin close')
            return real_pin_close(pin)
        with mock.patch.object(watch,'_close_handle',new=fail_read), mock.patch.object(PinnedObject,'close',new=fail_stop):
            with self.assertRaises((ExactObjectOwnershipError,watch.WatchProtocolOwnershipError)) as raised:
                watch._durable_worker_exit(request,claim,launch,captured)
        self.assertEqual({request.stop_token_path,request.evidence_root/'terminal.json'},
                         {pin.path for pin in raised.exception.verification})
        self.assertEqual(2,len(raised.exception.verification))
        raised.exception.resolve()
        self.assertEqual((),raised.exception.retained_objects)


    def test_restart_after_dead_worker_publishes_distinct_recovery_stop(self):
        import json,subprocess,sys,time
        code="""import json
from tests.test_windows_watch_causal import CausalWatchTests
case=CausalWatchTests()
request,path,pid,low=case._case()
print(json.dumps([str(path),pid]),flush=True)
"""
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,check=True)
        raw_path,pid=json.loads(result.stdout)
        path=Path(raw_path)
        request=watch._load_request_path(path)
        launch=watch._load_worker_launch(request)
        deadline=time.monotonic()+15
        while True:
            status,handle,_=watch._exact_process_status(pid,launch.worker_creation_time)
            if handle:
                self.assertIsNone(watch._close_handle(handle,'test dead watcher observation'))
            if status=='dead':
                break
            self.assertLess(time.monotonic(),deadline)
            time.sleep(.02)
        self.assertFalse(watch.stop_watch(path).complete)
        self.assertTrue(request.stop_token_path.exists(),'restart must publish canonical recovery even after worker exits')
        self.assertEqual('RecoveryCleanupStop',json.loads(request.stop_token_path.read_bytes())['kind'])


    def _session_guard_partial_close_retry(self, attribute):
        from unittest import mock
        from modlab.platform.windows_exact_fs import PinnedObject, ExactObjectOwnershipError
        request,path,pid,low=self._case()
        self._launch(path,low)
        watch.complete_watch_launch(path)
        session=watch._local_launch_session(path)
        guard=getattr(session,attribute)
        retained_pin=guard._pins[-1]
        real_close=PinnedObject.close
        def fail_one_pin(pin):
            if pin is retained_pin:
                raise OSError('injected session guard partial close')
            return real_close(pin)
        try:
            with mock.patch.object(PinnedObject,'close',new=fail_one_pin):
                with self.assertRaises((ExactObjectOwnershipError,watch.WatchProtocolOwnershipError)):
                    watch.stop_watch(path)
            self.assertIs(guard,getattr(session,attribute))
            self.assertEqual([retained_pin],[pin for pin in guard._pins if pin.handle])
            self.assertIn('session-resource-close-failed',session.poison_reasons)
            try:
                receipt=watch.stop_watch(path)
            except BaseException as error:
                self.fail(f'{attribute} cleanup retry did not release remaining pins: {error}')
            self.assertFalse(receipt.complete)
            self.assertIn('session-resource-close-failed',receipt.error)
            self.assertTrue(all(not pin.handle for pin in guard._pins))
            self.assertIsNone(getattr(session,attribute))
            self.assertNotIn(path.absolute(),watch._LOCAL_SESSIONS)
            self.assertFalse((request.evidence_root/'worker-exit.json').exists())
            self.assertFalse(self._read(request,pid).complete)
        finally:
            # RED cleanup releases real handles without changing the assertion.
            guard.close()
            setattr(session,attribute,None)

    def test_session_runtime_guard_partial_close_retries_remaining_native_pin(self):
        self._session_guard_partial_close_retry('runtime_guard')

    def test_session_vault_partial_close_retries_remaining_native_pin(self):
        self._session_guard_partial_close_retry('vault')


    def test_session_guard_verification_failure_poison_does_not_skip_native_cleanup(self):
        from unittest import mock
        from modlab.platform.windows_exact_fs import ExactObjectError
        request,path,pid,low=self._case()
        self._launch(path,low)
        watch.complete_watch_launch(path)
        session=watch._local_launch_session(path)
        runtime=session.runtime_guard
        vault=session.vault
        try:
            with mock.patch.object(runtime,'verify',side_effect=ExactObjectError('injected guard verification failure')):
                with self.assertRaises(ExactObjectError):
                    watch.stop_watch(path)
            self.assertTrue(all(not pin.handle for pin in runtime._pins),
                            'verification failure skipped runtime guard cleanup')
            self.assertTrue(all(not pin.handle for pin in vault._pins))
            self.assertIn('session-guard-verification-failed',session.poison_reasons)
            self.assertFalse((request.evidence_root/'worker-exit.json').exists())
            self.assertFalse(self._read(request,pid).complete)
        finally:
            runtime.close()
            vault.close()
            session.runtime_guard=None
            session.vault=None
