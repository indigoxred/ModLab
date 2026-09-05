"""Native scoped store/service authority integration; disposable children only."""
import os
from pathlib import Path
import tempfile
import unittest
from modlab.platform import windows_exact_fs as exact
from modlab.validation.mo2_containment_store import ContainmentStore, ContainmentStoreError
from modlab.validation.windows_vault_security import create_vault, open_vault
from modlab.validation.mo2_containment_model import ContainmentScenario

RUN = "containment-run:" + "a" * 32
SCENARIO = ContainmentScenario.MERGE_EXISTING

@unittest.skipUnless(os.name == "nt", "native Windows authority boundary")
class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ev-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = ContainmentStore(self.base / "validation")

    def test_fresh_run_separates_disposable_and_authoritative_paths(self):
        self.store.prepare_run_root(RUN)
        self.assertTrue(hasattr(self.store, "evidence_run_path"), "explicit authoritative run path required")
        self.assertNotEqual(self.store.run_path(RUN), self.store.evidence_run_path(RUN))
        with open_vault(self.store.evidence_run_path(RUN)) as vault:
            self.assertEqual(vault.path / "request.json", self.store.request_path(RUN))
        self.assertEqual(self.store.run_path(RUN) / "quarantine", self.store.quarantine_path(RUN))

    def test_authority_collection_stays_pinned_during_run_enumeration(self):
        from unittest import mock
        from modlab.validation import mo2_containment_store as module
        self.store.prepare_run_root(RUN)
        root=self.store.evidence_root
        moved=root.with_name(root.name+"-moved")
        scan=os.scandir
        denied=[]
        def try_move(path):
            if Path(path)==root:
                try:
                    root.rename(moved)
                except OSError:
                    denied.append(True)
                else:
                    moved.rename(root)
            return scan(path)
        with mock.patch.object(module.os,"scandir",side_effect=try_move):
            self.assertEqual((RUN,),self.store.list_run_ids())
        self.assertEqual([True],denied,"collection enumeration lost its original native root guard")

    def test_legacy_disposable_run_is_not_enumerated_as_new_authority(self):
        self.store.run_path(RUN).mkdir()
        (self.store.run_path(RUN) / "intent.json").write_bytes(b"historical")
        self.assertEqual((), self.store.list_run_ids(), "legacy disposable-only runs must not block fresh preparation")
        self.assertEqual(b"historical", (self.store.run_path(RUN) / "intent.json").read_bytes())

    def test_historical_run_cannot_be_prepared_or_created(self):
        from dataclasses import replace
        from tests.test_mo2_containment_store import valid_prepared_journal
        for index, operation in enumerate(("prepare", "create")):
            with self.subTest(operation=operation):
                run_id = "containment-run:" + str(index + 1) * 32
                historical = self.store.run_path(run_id)
                historical.mkdir()
                intent = historical / "intent.json"
                intent.write_bytes(b"historical immutable intent")
                original = exact.identity_at_path(historical)
                with self.assertRaisesRegex(ContainmentStoreError, "historical"):
                    if operation == "prepare":
                        self.store.prepare_run_root(run_id)
                    else:
                        self.store.create(replace(valid_prepared_journal(self.base), run_id=run_id))
                self.assertFalse(self.store.evidence_run_path(run_id).exists())
                self.assertEqual(original, exact.identity_at_path(historical))
                self.assertEqual((intent,), tuple(historical.iterdir()))
                self.assertEqual(b"historical immutable intent", intent.read_bytes())

    def test_verified_new_run_can_be_prepared_and_created_again(self):
        from dataclasses import replace
        from tests.test_mo2_containment_store import valid_prepared_journal
        self.store.prepare_run_root(RUN)
        original = exact.identity_at_path(self.store.evidence_run_path(RUN))
        journal = replace(valid_prepared_journal(self.base), run_id=RUN)
        reopened = ContainmentStore(self.store.root)
        reopened.prepare_run_root(RUN)
        self.assertEqual(journal, reopened.create(journal))
        self.assertEqual(journal, reopened.create(journal))
        self.assertEqual(original, exact.identity_at_path(reopened.evidence_run_path(RUN)))

    def test_unverified_existing_run_is_never_adopted(self):
        self.assertTrue(hasattr(self.store, "evidence_run_path"), "explicit authority required")
        path = self.store.evidence_run_path(RUN)
        path.mkdir(parents=True)
        with self.assertRaises((ContainmentStoreError, exact.ExactObjectError)):
            self.store.prepare_run_root(RUN)
        with self.assertRaises((ContainmentStoreError, exact.ExactObjectError)):
            self.store.list_run_ids()

    def test_exact_capture_retains_original_bytes_after_low_source_changes(self):
        self.assertTrue(hasattr(self.store, "capture_evidence_file"), "bounded protected capture required")
        from modlab.validation.windows_integrity import set_low_integrity_tree
        low = self.base / "low"
        low.mkdir()
        source = low / "screenshot.png"
        original = b"\x89PNG\r\n\x1a\noriginal"
        source.write_bytes(original)
        set_low_integrity_tree(low)
        self.store.prepare_run_root(RUN)
        captured = self.store.evidence_run_path(RUN) / "screenshot.png"
        receipt = self.store.capture_evidence_file(RUN, source, captured, maximum_bytes=100, stage="after-close")
        source.write_bytes(b"changed")
        self.assertEqual(original, self.store.read_evidence_file(RUN, captured, maximum_bytes=100))
        self.assertEqual(len(original), receipt["byteCount"])
        self.assertEqual("after-close", receipt["stage"])
        self.assertNotEqual(exact.identity_at_path(source), exact.identity_at_path(captured))
        with self.assertRaises(ContainmentStoreError):
            self.store.read_evidence_file(RUN, source, maximum_bytes=100)
        with open_vault(self.store.evidence_run_path(RUN)) as vault:
            vault.verify_descendant(captured)

    def test_capture_refuses_reparse_source_ancestor_before_publication(self):
        from modlab.validation.windows_junction import create_owned_projection
        low=self.base/"origin"/"source"
        (low/"nested").mkdir(parents=True)
        (low/"nested"/"log.txt").write_bytes(b"original")
        link=self.base/"links"/"redirect"
        link.parent.mkdir()
        owner=create_owned_projection(low,link)
        owner.close()
        self.store.prepare_run_root(RUN)
        target=self.store.evidence_run_path(RUN)/"log.txt"
        try:
            with self.assertRaises((exact.ExactObjectError,ContainmentStoreError)):
                self.store.capture_evidence_file(RUN,link/"nested"/"log.txt",target,maximum_bytes=100,stage="after-close")
            self.assertFalse(target.exists())
        finally:
            link.rmdir()

    def test_outcome_only_forgery_is_rejected_without_raw_causal_records(self):
        from dataclasses import replace
        from tests.test_mo2_containment_store import valid_watch_outcome
        outcome = replace(valid_watch_outcome(), run_id=RUN)
        with self.assertRaises(ContainmentStoreError):
            self.store.write_watch_outcome(outcome)

    def test_overlapping_roots_rejected_before_any_creation(self):
        for disposable, authority in ((self.base/"equal", self.base/"equal"),
                (self.base/"outer", self.base/"outer"/"inner"),
                (self.base/"parent"/"inner", self.base/"parent")):
            with self.assertRaisesRegex(ContainmentStoreError, "separate"):
                ContainmentStore(disposable, authority_root=authority)
            self.assertFalse(disposable.exists())
            self.assertFalse(authority.exists())

    def test_watcher_effect_observer_records_exact_partial_writes_and_original_owner(self):
        from modlab.validation import mo2_containment_service as service
        from tests.test_mo2_containment_store import valid_prepared_journal
        from dataclasses import replace
        self.store.create(replace(valid_prepared_journal(self.base),run_id=RUN))
        target=self.store.watch_path(RUN,SCENARIO)/"partial.json"
        cause=RuntimeError("partial watcher publication")
        cause.owner=object()
        def operation():
            target.write_bytes(b"partial")
            raise cause
        @service._receipted
        def invoke():
            return service._delegated_watch_mutation(self.store,RUN,SCENARIO,operation)
        with self.assertRaises(service.ContainmentOperationError) as raised:
            invoke()
        self.assertIs(cause.owner,raised.exception.owner)
        self.assertIn(target,raised.exception.effects.written_paths)
        self.assertIn(target.parent,raised.exception.effects.child_mutation_roots)

    def test_fresh_ancestor_close_failure_retains_original_created_handle(self):
        from unittest import mock
        target=self.base/"new-parent"
        retained=[]
        returned=[]
        from modlab.validation import mo2_containment_store as store_module
        create=store_module.create_pinned_directory_child
        def create_and_retain(*args,**kwargs):
            value=create(*args,**kwargs)
            if value.path==target: returned.append(value)
            return value
        close=exact.PinnedObject.close
        def fail_once(pin):
            if returned and pin is returned[0] and not retained:
                retained.append(pin)
                raise OSError("injected created ancestor close")
            return close(pin)
        try:
            with mock.patch.object(exact.PinnedObject,"close",new=fail_once), mock.patch.object(store_module,"create_pinned_directory_child",side_effect=create_and_retain):
                with self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                    ContainmentStore(target/"validation")
            self.assertIn(retained[0],raised.exception.retained_objects)
            raised.exception.resolve()
            self.assertEqual(0,retained[0].handle)
        finally:
            for pin in retained: close(pin)

    def test_watcher_observer_unions_partial_operation_observation_and_guard_ownership(self):
        from unittest import mock
        from dataclasses import replace
        from modlab.validation import mo2_containment_service as service
        from tests.test_mo2_containment_store import valid_prepared_journal
        self.store.create(replace(valid_prepared_journal(self.base),run_id=RUN))
        root=self.store.watch_path(RUN,SCENARIO)
        first=root/"partial.json"
        second=root/"observation.json"
        second.write_bytes(b"observation")
        native=[]
        operation_error=None
        process_owner=object()
        close=exact.PinnedObject.close
        failed_guard=[]
        observe=service._watch_effect_observation
        observations=0
        def fail_guard(pin):
            if pin.path==self.store.evidence_run_path(RUN) and operation_error is not None and not failed_guard:
                failed_guard.append(pin)
                raise OSError("injected vault guard close")
            return close(pin)
        def operation():
            nonlocal operation_error
            first.write_bytes(b"partial")
            native.append(exact.pin_direct_object(first,"file",delete_access=False))
            operation_error=service.ContainmentStoreOwnershipError("operation retained publication",exact.ExactObjectOwnershipError("original",verification=(native[0],)))
            operation_error.owner=process_owner
            raise operation_error
        def observe_then_fail(vault,path):
            nonlocal observations
            observations+=1
            result=observe(vault,path)
            if observations==2:
                native.append(exact.pin_direct_object(second,"file",delete_access=False))
                raise exact.ExactObjectOwnershipError("observation retained handle",verification=(native[1],))
            return result
        @service._receipted
        def invoke(): return service._delegated_watch_mutation(self.store,RUN,SCENARIO,operation)
        try:
            with mock.patch.object(service,"_watch_effect_observation",side_effect=observe_then_fail), mock.patch.object(exact.PinnedObject,"close",new=fail_guard):
                with self.assertRaises(BaseException) as raised: invoke()
            error=raised.exception
            self.assertIs(process_owner,service._process_owner_from_error(error))
            ownership=error if isinstance(error,exact.ExactObjectOwnershipError) else error.cause
            self.assertIsInstance(ownership,exact.ExactObjectOwnershipError)
            self.assertEqual({id(pin) for pin in (*native,*failed_guard)}, {id(pin) for pin in ownership.retained_objects})
            ownership.resolve()
            self.assertTrue(all(pin.handle==0 for pin in (*native,*failed_guard)))
        finally:
            for pin in (*native,*failed_guard): close(pin)

    def test_store_guard_unions_publication_and_guard_close_ownership(self):
        from unittest import mock
        from modlab.validation import mo2_containment_service as service
        self.store.prepare_run_root(RUN)
        target=self.store.evidence_run_path(RUN)/"original.json"
        target.write_bytes(b"original")
        publication=exact.pin_direct_object(target,"file",delete_access=False)
        failure=service.ContainmentStoreOwnershipError("publication cleanup",exact.ExactObjectOwnershipError("original publication",verification=(publication,)))
        close=exact.PinnedObject.close
        failed=[]
        active=[]
        def fail_guard(pin):
            if active and pin.path==self.store.evidence_run_path(RUN) and not failed:
                failed.append(pin)
                raise OSError("vault guard close failure")
            return close(pin)
        try:
            with mock.patch.object(exact.PinnedObject,"close",new=fail_guard):
                with self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                    with self.store._evidence_guard(self.store.evidence_run_path(RUN)):
                        active.append(True)
                        raise failure
            self.assertEqual({id(publication),id(failed[0])},{id(pin) for pin in raised.exception.retained_objects})
            raised.exception.resolve()
        finally:
            for pin in (publication,*failed): close(pin)

    def test_recovery_proof_propagates_owned_native_read_failure(self):
        from unittest import mock
        from dataclasses import replace
        from modlab.validation import mo2_containment_service as service
        from tests.test_mo2_containment_store import valid_prepared_journal
        from tests.test_mo2_containment_service import fixture_record
        journal=self.store.create(replace(valid_prepared_journal(self.base),run_id=RUN))
        record=fixture_record(self.base,journal.scenario)
        path=self.store.journal_path(RUN,journal.scenario)
        pin=exact.pin_direct_object(path,"file",delete_access=False)
        failure=exact.ExactObjectOwnershipError("native read close failed",verification=(pin,))
        try:
            read=self.store._read
            def fail_watch(path,label):
                if path.name=="request.json": raise failure
                return read(path,label)
            from types import SimpleNamespace
            with mock.patch.object(self.store,"_read",side_effect=fail_watch), mock.patch.object(service,"inspect_mo2_processes",return_value=SimpleNamespace(complete=True,relevant=())), mock.patch.object(service,"_capture_protected",return_value=journal.protected_before):
                with self.assertRaises(exact.ExactObjectOwnershipError) as raised:
                    service._prove_recovery(self.store,record,journal)
            self.assertIs(failure,raised.exception)
        finally:
            pin.close()

    def test_service_wrapper_keeps_explicit_original_process_owner(self):
        from modlab.validation.mo2_containment_service import ContainmentOperationError, ContainmentEffects
        cause = RuntimeError("partial launch")
        cause.owner = object()
        error = ContainmentOperationError("launch failed", effects=ContainmentEffects(), cause=cause)
        self.assertIs(cause.owner, getattr(error, "owner", None), "service wrapper must retain error.owner")

    def test_result_requires_independent_evaluation_inputs(self):
        self.assertTrue(hasattr(self.store, "write_evaluation"), "protected independent evaluation input required")

class EvidenceInventoryTests(unittest.TestCase):
    def test_inventory_covers_all_bound_inputs_and_separates_later_gate_consumers(self):
        import json
        rows=json.loads((Path(__file__).resolve().parents[1]/"docs/validation/mo2-containment-evidence-inventory.json").read_text())
        expected={
            *("watch/"+name for name in ("request.json","controller-claim.json","worker-launch.json","ready.json","events.ndjson","terminal.json","controller-loss.json","stop.token","launch-admission.json","process-quiescence.json","worker-exit.json","outcome.json")),
            *("scenario/"+name for name in ("journal.json","started.json","launch.json","before.json","after.json","evaluation.json","result.json","recovery.json","retry.json","retry-consumption.json")),
            *("run/"+name for name in ("intent.json","request.json","decision.json","preparation-attempt.json","preparation-failure.json","preparation-recovery.json","preparation-replacement.json","preparation-startup-initialized.json","preparation-startup-failure.json")),
            *("catalog/"+name for name in ("retirements","supersessions","reviews","eligibilities")),
            *("gate/"+name for name in ("selection","installation","envelope","phase-begin","process","operator","runtime","observation","effect","guarded-phase","final","failure","cleanup","restart")),
            "preparation-projection","preparation-projection-cleanup","capture/screenshot.png","capture/log.txt",
        }
        self.assertEqual(expected,{row["input"] for row in rows})
        self.assertEqual(len(expected),len(rows))
        for row in rows:
            self.assertTrue(row["capturePath"].startswith("authority/"),row)
            self.assertNotEqual(row["sourcePath"],row["capturePath"])
            if row["input"].startswith("gate/"): self.assertIn("Task5 integration required",row["consumer"])


class ExactCaptureReaderTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native exact reader")
    def test_bound_is_checked_before_reading_any_bytes(self):
        from unittest import mock
        with tempfile.TemporaryDirectory(prefix="eb-") as directory:
            path=Path(directory)/"input"
            path.write_bytes(b"abcdef")
            pin=exact.pin_stable_direct_object(path,"file")
            try:
                with mock.patch.object(exact._kernel32, "ReadFile", side_effect=AssertionError("oversize content read")):
                    with self.assertRaisesRegex(exact.ExactObjectError, "bound|maximum"):
                        exact.read_pinned_file(pin, maximum_bytes=5)
            finally:
                pin.close()

    @unittest.skipUnless(os.name == "nt", "native exact reader")
    def test_readonly_directory_pin_coexists_with_vault_and_denies_rename(self):
        with tempfile.TemporaryDirectory(prefix="ep-") as directory:
            root=Path(directory)
            with create_vault(root/"vault") as vault:
                pin=exact.pin_stable_direct_object(vault.path,"directory",delete_access=False,allow_writes=True)
                try:
                    with self.assertRaises(OSError):
                        vault.path.rename(root/"renamed")
                finally:
                    pin.close()

@unittest.skipUnless(os.name == "nt", "native causal store integration")
class CausalStoreTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        from modlab.validation import windows_watch as watch
        from modlab.validation.windows_integrity import set_low_integrity_tree
        from tests.test_mo2_containment_store import valid_prepared_journal
        self.watch = watch
        self.temp = tempfile.TemporaryDirectory(prefix="cs-")
        self.base = Path(self.temp.name)
        self.store = ContainmentStore(self.base / "validation")
        self.journal = self.store.create(replace(valid_prepared_journal(self.base), run_id=RUN))
        watched = self.base / "watched"
        watched.mkdir()
        self.low = self.base / "low"
        self.low.mkdir()
        set_low_integrity_tree(self.low)
        self.root = self.store.watch_path(RUN, SCENARIO)
        physical = watch.watch_root("SourceMods", watched)
        with self.store.open_evidence_vault(RUN) as vault:
            self.request = watch.WatchRequest("watch-request:"+"1"*64,"watch-session:"+"2"*64,
                RUN,SCENARIO,self.root,self.root/"stop.token",
                tuple(replace(physical,root_kind=kind) for kind in watch.ROOT_KINDS),
                vault.path,vault.identity.volume_serial,vault.identity.file_id,vault.creator_sid)
            self.pid = watch.start_watch(self.request)
        self.journal = self.store.transition(self.journal, __import__("modlab.validation.mo2_containment_model",fromlist=["ScenarioState"]).ScenarioState.ARMED,monitor_pid=self.pid)
        self.owner = None

    def tearDown(self):
        import time
        if self.owner is not None:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                try:
                    if self.owner.observe().active_processes == 0:
                        break
                except RuntimeError:
                    break
                time.sleep(.02)
        try:
            self.watch.stop_watch(self.root/"request.json",recovery=True)
        finally:
            if self.owner is not None:
                self.owner.close()
            self.temp.cleanup()

    def launch(self, code="pass"):
        import sys, time
        from dataclasses import replace
        from modlab.validation.windows_integrity import launch_low_integrity_process
        from modlab.validation.mo2_containment_model import ScenarioState
        from tests.test_mo2_containment_service import process
        self.journal=self.store.transition(self.journal,ScenarioState.SCENARIO_STARTED)
        launch=launch_low_integrity_process(Path(sys.executable),("-B","-c",code),self.low,
            dict(os.environ,TEMP=str(self.low),TMP=str(self.low)),retain_owner=True,
            before_resume=lambda value:self.watch.admit_watch_launch(self.root/"request.json",value))
        self.owner=launch.owner
        self.process=replace(process(),pid=launch.pid)
        self.store.write_launch_evidence(RUN,SCENARIO,{"schemaVersion":1,"runId":RUN,"scenario":SCENARIO.value,
            "purpose":"stage-mo2-scenario","pid":launch.pid,"creationTime":launch.creation_time,
            "executable":self.process.executable,"executableVersion":self.process.executable_version,
            "arguments":list(self.process.arguments),"workingDirectory":self.process.working_directory,"integrity":"Low"})
        self.journal=self.store.transition(self.journal,ScenarioState.LAUNCHED,mo2_pid=launch.pid)
        deadline=time.monotonic()+10
        while self.owner.observe().root_exit_code is None:
            if time.monotonic()>deadline: self.fail("root did not exit")
            time.sleep(.02)
        return launch

    def finish(self, recovery=False):
        import time
        from modlab.validation import mo2_containment_service as service
        while self.owner.observe().active_processes:
            time.sleep(.02)
        receipt=service._complete_and_stop_watch(self.root/"request.json",recovery=recovery)
        return self.store.load_watch_outcome(RUN,SCENARIO,receipt.watch_outcome_id)

    def test_completed_raw_store_result_reconstructs_and_missing_worker_exit_rejects(self):
        from dataclasses import replace
        from tests.test_mo2_containment_service import evidence
        from modlab.validation.mo2_containment_service import evaluate_scenario
        self.launch()
        outcome=self.finish()
        value=evidence(SCENARIO,run_id=RUN,watch_outcome=outcome,mo2_process=self.process)
        self.store.write_evaluation(value)
        result=evaluate_scenario(value)
        self.store.write_result(result)
        reopened=ContainmentStore.open_readonly(self.store.root)
        self.assertEqual(result,reopened.load_result(RUN,SCENARIO))
        (self.root/"worker-exit.json").unlink()
        with self.assertRaises(ContainmentStoreError):
            reopened.load_result(RUN,SCENARIO)

    def assert_valid_launch_breach_survives_refused_recovery(self, cleanup_refusal=False):
        from dataclasses import replace
        from unittest import mock
        from modlab.validation import mo2_containment_service as service
        from modlab.validation.mo2_containment_model import ScenarioCleanupStatus, ScenarioOutcome
        self.launch()
        outcome = self.finish()
        after = replace(self.journal.protected_before, play_profile_sha256="b" * 64)
        blocker = "cleanup-proof-unavailable" if cleanup_refusal else "prior-mo2-process-still-live"
        proof = service.RecoveryProofEvidence(outcome, after, () if cleanup_refusal else (blocker,))
        cleanup_result = service.RecoveryProofEvidence(outcome, after, (blocker,))
        quarantine = self.store.quarantine_path(RUN)
        before = tuple(quarantine.iterdir())
        with mock.patch.object(service, "_load_fixture_record", return_value=object()), \
             mock.patch.object(service, "_prove_recovery", return_value=proof), \
             mock.patch.object(service, "_perform_recovery_cleanup", return_value=cleanup_result) as cleanup:
            receipt = service.recover_scenario(self.store.root, RUN, SCENARIO)
        if cleanup_refusal:
            cleanup.assert_called_once()
        else:
            cleanup.assert_not_called()
        recovery = receipt.value
        self.assertIs(ScenarioCleanupStatus.REFUSED, recovery.cleanup_status)
        self.assertEqual((blocker,), recovery.blockers)
        self.assertFalse(recovery.fresh_run_permitted)
        self.assertIsNotNone(recovery.result_id)
        reopened = ContainmentStore.open_readonly(self.store.root)
        result = reopened.load_result(RUN, SCENARIO)
        self.assertIs(ScenarioOutcome.FAILED, result.outcome)
        self.assertEqual(self.process, result.mo2_process)
        self.assertEqual(recovery, reopened.load_recovery(RUN, SCENARIO))
        self.assertNotEqual(reopened.result_path(RUN, SCENARIO), reopened.scenario_path(RUN, SCENARIO) / "recovery.json")
        self.assertEqual(before, tuple(quarantine.iterdir()))
        self.assertEqual((), receipt.effects.child_mutation_roots)

    def test_valid_launch_breach_persists_before_refused_cleanup(self):
        self.assert_valid_launch_breach_survives_refused_recovery()

    def test_valid_launch_breach_persists_after_cleanup_proof_refusal(self):
        self.assert_valid_launch_breach_survives_refused_recovery(cleanup_refusal=True)

    def test_malformed_raw_request_has_typed_store_refusal(self):
        self.launch()
        self.finish()
        original=(self.root/"request.json").read_bytes()
        try:
            (self.root/"request.json").write_bytes(b"{}\n")
            with self.assertRaises(ContainmentStoreError):
                self.store.load_watch_outcome(RUN,SCENARIO)
        finally:
            (self.root/"request.json").write_bytes(original)

    def test_result_reconstructs_original_observations_instead_of_verdict_fields(self):
        import json
        from tests.test_mo2_containment_service import evidence
        from modlab.validation.mo2_containment_service import evaluate_scenario
        self.launch()
        outcome=self.finish()
        value=evidence(SCENARIO,run_id=RUN,watch_outcome=outcome,mo2_process=self.process)
        self.store.write_evaluation(value)
        self.store.write_result(evaluate_scenario(value))
        path=self.store.evaluation_path(RUN,SCENARIO)
        document=json.loads(path.read_bytes())
        document["observations"]["projection_payload_bytes_copied"]=10
        path.write_bytes((json.dumps(document,sort_keys=True,separators=(",",":"))+"\n").encode())
        with self.assertRaises(ContainmentStoreError):
            ContainmentStore.open_readonly(self.store.root).load_result(RUN,SCENARIO)

    def test_recovery_only_attempt_is_never_complete(self):
        from modlab.validation.mo2_containment_model import WatchEvidenceCompletion
        self.launch()
        outcome=self.finish(recovery=True)
        self.assertIs(WatchEvidenceCompletion.INCOMPLETE,outcome.evidence_completion)

    def test_live_grandchild_blocks_capture_and_recovery_before_relocation(self):
        from types import SimpleNamespace
        from unittest import mock
        from modlab.validation import mo2_containment_service as service
        code="import subprocess,sys; subprocess.Popen([sys.executable,'-B','-c',\"import subprocess,sys; subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(3)'])\"])"
        self.launch(code)
        self.assertGreater(self.owner.observe().active_processes,0)
        record=SimpleNamespace(stage_root=Path(self.journal.stage_root),source_root=Path(self.journal.source_root),
            archive_path=Path(self.journal.archive_path),scenario=SCENARIO,executable=Path(self.process.executable),stage_app=Path(self.process.working_directory))
        with mock.patch.object(service,"_load_fixture_record",return_value=record), \
             mock.patch.object(service,"_require_current_execution_policy"), \
             mock.patch.object(service,"inspect_mo2_processes",return_value=SimpleNamespace(complete=True,relevant=())), \
             mock.patch.object(service,"_exact_process_absent",return_value=True), \
             mock.patch.object(service,"_capture_protected",return_value=self.journal.protected_before) as protected, \
             mock.patch.object(service,"_finalize_projection",side_effect=AssertionError("live relocation")), \
             mock.patch.object(service,"_perform_recovery_cleanup",side_effect=AssertionError("live cleanup")):
            with self.assertRaises(service.ContainmentOperationError) as captured:
                service.capture_scenario(self.store.root,RUN,SCENARIO)
            self.assertIs(self.owner,captured.exception.owner, repr(captured.exception)+" cause="+repr(captured.exception.__cause__))
            protected.assert_not_called()
            with self.assertRaises(service.ContainmentOperationError) as recovered:
                service.recover_scenario(self.store.root,RUN,SCENARIO)
            self.assertIs(self.owner,recovered.exception.owner)
        self.assertFalse((self.root/"stop.token").exists())
