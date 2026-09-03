"""Preparation lifecycle tests: real storage and native owner/process boundaries."""
import importlib.util
from contextlib import contextmanager
import os
import json
from pathlib import Path
import subprocess
import secrets
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from modlab.validation import mo2_containment_service as service
from modlab.validation.mo2_containment_store import ContainmentStore, ContainmentStoreError


@contextmanager
def _short_native_workspace():
    """Exclusive test-owned short root; never reuse/remove a preexisting entry."""
    worktree = Path(__file__).resolve().parents[1]
    for _ in range(64):
        candidate = worktree / ("t" + secrets.token_hex(1))
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        break
    else:
        raise RuntimeError("could not allocate a short exclusive native test root")
    created_identity = (candidate.stat().st_dev, candidate.stat().st_ino)
    try:
        yield candidate
    finally:
        if (candidate.parent != worktree or candidate.is_junction() or candidate.is_symlink()
                or (candidate.stat().st_dev, candidate.stat().st_ino) != created_identity):
            raise RuntimeError("test root identity is unsafe for test cleanup")
        # Python's Windows rmtree removes junctions without traversing targets.
        shutil.rmtree(candidate)


def _native_child_failure(source, artifact, steam, validation):
    """Real fixture failure after its genuine guarded post-observation."""
    real = service._mutation_root_observation
    injected = False
    def fail_once(root, **kwargs):
        nonlocal injected
        observed = real(root, **kwargs)
        if kwargs.get("expected_projections") and not injected:
            injected = True
            raise service._MutationObservationError("controlled failure after genuine projection observation")
        return observed
    with patch.object(service, "_mutation_root_observation", side_effect=fail_once):
        try:
            service.prepare_run(Path(source), artifact, Path(steam), Path(validation))
        except service.ContainmentServiceError:
            if not injected:
                raise
        else:
            raise AssertionError("controlled native preparation unexpectedly succeeded")
    store = ContainmentStore.open_readonly(Path(validation))
    run_id, = store.list_run_ids()
    failure = store.load_preparation_failure(run_id)
    assert len(failure.projection_ids) == 1, failure.reason
    print(json.dumps({"runId": run_id, "pid": os.getpid()}))


@unittest.skipUnless(os.name == "nt" and os.environ.get("MODLAB_PREPARATION_ARCHIVE") and os.environ.get("MODLAB_PREPARATION_STEAM"),
                     "all-real preparation regression requires explicit read-only archive and Steam inputs")
class RealPreparationRestartTests(unittest.TestCase):
    def test_foreground_failed_attempt_recovers_once_into_completely_fresh_real_preparation(self):
        from modlab.workspace import initialize_workspace
        from tests.support.mo2_containment import import_curated_mo2_archive
        archive_input = Path(os.environ["MODLAB_PREPARATION_ARCHIVE"])
        steam_input = Path(os.environ["MODLAB_PREPARATION_STEAM"])
        native_environment = {key: os.environ[key] for key in ("COMSPEC", "PATH", "SYSTEMROOT", "TEMP", "TMP", "USERNAME", "WINDIR") if key in os.environ}
        # Keep system tar's extraction cwd below the native legacy path limit.
        with _short_native_workspace() as root:
            source = root
            # The fixture verifies an existing Low-integrity TEMP/Low. Own that
            # prerequisite in this test instead of relying on the caller's TEMP.
            from modlab.validation.windows_integrity import set_low_integrity_tree
            native_temp = root / "native-temp"
            (native_temp / "Low").mkdir(parents=True)
            set_low_integrity_tree(native_temp / "Low")
            native_environment["TEMP"] = str(native_temp)
            native_environment["TMP"] = str(native_temp)
            layout = initialize_workspace(source)
            artifact = import_curated_mo2_archive(archive_input, source, root / "curated-input")
            validation = layout.mo2_containment_validation
            code = "from tests.test_mo2_preparation_recovery import _native_child_failure; import sys; _native_child_failure(*sys.argv[1:])"
            child = subprocess.run([sys.executable, "-B", "-c", code, str(source), artifact.artifact_id, str(steam_input), str(validation)],
                env=native_environment, capture_output=True, text=True, timeout=240)
            self.assertEqual(0, child.returncode, child.stdout + child.stderr)
            old_id = json.loads(child.stdout)["runId"]
            store = ContainmentStore.open_readonly(validation)
            old_attempt = store.load_preparation_attempt(old_id)
            old_files = {path: path.read_bytes() for path in store.run_path(old_id).glob("*.json")}
            from modlab.validation.mo2_preparation_recovery import native_process_absent
            self.assertTrue(native_process_absent(old_attempt.controller_pid, old_attempt.controller_creation_time))
            with patch.dict(os.environ, native_environment, clear=True):
                recovered = service.recover_preparation(validation, old_id)
                self.assertTrue(recovered.value.fresh_run_permitted)
                restarted = service.restart_preparation(validation, old_id)
                with self.assertRaises(service.ContainmentServiceError):
                    service.restart_preparation(validation, old_id)
            fresh_id = restarted.value
            self.assertNotEqual(old_id, fresh_id)
            new_attempt = store.load_preparation_attempt(fresh_id)
            self.assertNotEqual(old_attempt.session_id, new_attempt.session_id)
            new_request = store.load_request(fresh_id)
            self.assertEqual(4, len(new_request["scenarios"]))
            self.assertEqual([old_id], new_request["predecessorRunIds"])
            self.assertTrue(all(Path(item["runRoot"]).is_relative_to(store.run_path(fresh_id)) for item in new_request["scenarios"]))
            self.assertTrue(all(Path(item["runRoot"]) in restarted.effects.child_mutation_roots for item in new_request["scenarios"]))
            for path, data in old_files.items():
                self.assertEqual(data, path.read_bytes())
            self.assertFalse(store.request_path(old_id).exists())
            self.assertFalse(store.decision_path(old_id).exists())
            self.assertEqual((), tuple((store.run_path(old_id) / "scenarios").iterdir()))


class PreparationRecoveryContractTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "native inventory requires Windows")
    def test_native_inventory_rejects_incomplete_enumeration_and_close_uncertainty(self):
        import ctypes
        from modlab.validation import mo2_preparation_recovery as recovery
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        def incomplete(*_args):
            ctypes.set_last_error(5)
            return 0
        with patch("ctypes.WinDLL", return_value=kernel), patch.object(kernel, "Process32NextW", side_effect=incomplete):
            with self.assertRaisesRegex(RuntimeError, "incompletely"):
                recovery.native_preparation_process_inventory()
        real_close = service._windows_watch._close_controller_handle
        def uncertain(handle, label, path):
            real_close(handle, label, path)
            return "injected close uncertainty"
        with patch.object(service._windows_watch, "_close_controller_handle", side_effect=uncertain):
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                recovery.native_preparation_process_inventory()

    @unittest.skipUnless(os.name == "nt", "native inventory requires Windows")
    def test_native_inventory_rejects_a_real_live_python_child(self):
        from modlab.validation.mo2_preparation_recovery import native_preparation_process_inventory
        with subprocess.Popen([sys.executable, "-B", "-c", "import sys; print('ready', flush=True); sys.stdin.readline()"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                self.assertEqual("ready\n", child.stdout.readline())
                with self.assertRaisesRegex(RuntimeError, "candidate is live or uncertain"):
                    native_preparation_process_inventory()
            finally:
                child.stdin.write("exit\n")
                child.stdin.flush()
                child.wait(timeout=10)

    @unittest.skipUnless(os.name == "nt", "native ownership requires Windows")
    def test_native_inventory_retains_both_process_and_snapshot_close_owners(self):
        from modlab.validation.mo2_preparation_recovery import native_preparation_process_inventory
        from modlab.platform import windows_exact_fs
        watch = service._windows_watch
        WatchProtocolOwnershipError = watch.WatchProtocolOwnershipError
        retained = None
        try:
            with patch.object(watch, "_close_handle", return_value="injected native close failure"), \
                    patch.object(windows_exact_fs, "_close_handle", side_effect=OSError("injected retained close failure")):
                with self.assertRaises(WatchProtocolOwnershipError) as raised:
                    native_preparation_process_inventory()
                retained = raised.exception
            self.assertEqual(2, len(retained.ownership.retained_objects))
        finally:
            if retained is not None:
                # Also resolve contextual owners if a broken implementation lost
                # the first owner while raising the second cleanup exception.
                errors = [retained]
                prior = retained.__context__
                while prior is not None and prior not in errors:
                    errors.append(prior)
                    prior = prior.__context__
                for error in errors:
                    if isinstance(error, WatchProtocolOwnershipError):
                        error.resolve()

    @unittest.skipUnless(os.name == "nt", "native process identity requires Windows")
    def test_native_process_absence_rejects_live_reused_and_access_denied_pid(self):
        from modlab.validation import mo2_preparation_recovery as recovery
        self.assertTrue(callable(getattr(recovery, "native_process_absent", None)), "preparation native absence API is missing")
        watch = service._windows_watch
        handle, created = watch._open_process_identity(os.getpid())
        watch._close_controller_handle(handle, "test identity", Path(__file__))
        self.assertFalse(recovery.native_process_absent(os.getpid(), created))
        self.assertFalse(recovery.native_process_absent(os.getpid(), created + 1))
        with patch.object(watch._kernel32, "WaitForSingleObject", return_value=watch._WAIT_OBJECT_0):
            self.assertFalse(recovery.native_process_absent(os.getpid(), created + 1))
        with patch.object(watch._kernel32, "WaitForSingleObject", return_value=watch._WAIT_FAILED):
            self.assertFalse(recovery.native_process_absent(os.getpid(), created))
        import ctypes
        def denied(*_args):
            ctypes.set_last_error(5)
            return 0
        with patch.object(watch._kernel32, "OpenProcess", side_effect=denied):
            self.assertFalse(recovery.native_process_absent(os.getpid(), created))

    def test_preparation_records_reject_noncanonical_and_cross_run_replay(self):
        self.assertIsNotNone(importlib.util.find_spec("modlab.validation.mo2_preparation_recovery"), "separate preparation schema is missing")
        from modlab.validation import mo2_preparation_recovery as recovery
        failure = recovery.PreparationFailure(
            run_id="containment-run:" + "a" * 32,
            intent_id="containment-intent-sha256:" + "b" * 64,
            command_fingerprint="containment-command-sha256:" + "c" * 64,
            attempt_id=None, effects=(), projection_ids=(),
            reason="legacy creation receipts unavailable", legacy=True,
        )
        with tempfile.TemporaryDirectory(prefix="modlab-preparation-record-", dir=Path(__file__).resolve().parents[1]) as directory:
            store = ContainmentStore(Path(directory))
            with self.assertRaises(ContainmentStoreError):
                store.write_preparation_failure(failure)  # no exact source intent
        encoded = recovery.record_to_bytes(failure)
        self.assertEqual(failure, recovery.record_from_bytes(encoded, recovery.PreparationFailure))
        with self.assertRaises(ValueError):
            recovery.record_from_bytes(encoded + b" ", recovery.PreparationFailure)
        with self.assertRaises(ValueError):
            recovery.record_from_bytes(encoded.replace(b'"legacy":true', b'"legacy":1'), recovery.PreparationFailure)

    def test_recovery_cannot_grant_retry_without_exact_process_and_failure_binding(self):
        self.assertIsNotNone(importlib.util.find_spec("modlab.validation.mo2_preparation_recovery"), "separate preparation schema is missing")
        from modlab.validation import mo2_preparation_recovery as recovery
        with self.assertRaises(ValueError):
            recovery.record_to_bytes(recovery.PreparationRecovery(
                run_id="containment-run:" + "a" * 32,
                intent_id="containment-intent-sha256:" + "b" * 64,
                command_fingerprint="containment-command-sha256:" + "c" * 64,
                failure_id="preparation-failure-sha256:" + "d" * 64,
                process_proof=(), cleaned=(), preserved=(), blockers=(),
                fresh_run_permitted=True,
            ))

    def test_restart_preparation_is_an_explicit_operation(self):
        self.assertTrue(callable(getattr(service, "restart_preparation", None)), "explicit fresh restart API is missing")
        with tempfile.TemporaryDirectory(prefix="modlab-preparation-absent-", dir=Path(__file__).resolve().parents[1]) as directory:
            absent = Path(directory) / "absent"
            with self.assertRaises((service.ContainmentServiceError, ContainmentStoreError)):
                service.restart_preparation(absent, "containment-run:" + "a" * 32)
            self.assertFalse(absent.exists())


@unittest.skipUnless(os.name == "nt", "native preparation recovery requires Windows")
class PreparationRecoveryAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modlab-prep-adversarial-", dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def failed_fixture(self, *, legacy=False):
        from modlab.validation import mo2_preparation_recovery as recovery
        from modlab.validation import windows_junction as junction
        from modlab.workspace import initialize_workspace
        layout = initialize_workspace(self.root / "workspace")
        store = ContainmentStore(layout.mo2_containment_validation)
        run_id = "containment-run:" + "a" * 32
        fingerprint = service._command_fingerprint(layout.root, "archive-sha256:" + "b" * 64, self.root / "Steam")
        intent = dict(schemaVersion=1, runId=run_id, mechanism="isolated-low-integrity-junction-projection-v1",
            sourceWorkspace=str(layout.root), mo2ArtifactId="archive-sha256:" + "b" * 64,
            steamRoot=str(self.root / "Steam"), commandFingerprint=fingerprint, predecessorRunIds=[], retryOf=None)
        intent_id = store.write_intent(run_id, intent).content_id
        fixture = store.run_path(run_id) / "fixtures/NewFolder"
        target = fixture / "source-workspace/tools/mo2/skyrim-se-ae/mods/Protected Existing"
        link = fixture / "stage-workspace/tools/mo2/skyrim-se-ae/mods/Protected Existing"
        target.mkdir(parents=True)
        link.parent.mkdir(parents=True)
        (target / "marker.txt").write_bytes(b"never change source payload")
        owner = junction.create_owned_projection(target, link)
        try:
            if not legacy:
                code = "import json; from modlab.validation.windows_watch import _current_controller_identity; print(json.dumps(_current_controller_identity()))"
                child = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, check=True)
                pid, created = json.loads(child.stdout)
                attempt = recovery.PreparationAttempt(run_id, intent_id, fingerprint, "c" * 40, "d" * 40,
                    "e" * 64, "preparation-session:" + "f" * 32, pid, created, None)
                store.write_preparation_attempt(attempt)
                (store.run_path(run_id) / "preparation-projections").mkdir()
                projection = recovery.PreparationProjection(run_id, intent_id, fingerprint, recovery.record_id(attempt), "NewFolder",
                    tuple(recovery.ProjectionIdentity(str(pin.path), *pin.identity) for pin in owner.pins), owner.payload.hex())
                store.write_preparation_projection(projection)
                failure = recovery.PreparationFailure(run_id, intent_id, fingerprint, recovery.record_id(attempt),
                    (str(fixture),), (recovery.record_id(projection),), "controlled native fixture failure", False)
                store.write_preparation_failure(failure)
        finally:
            owner.close()
        return store, run_id, fixture, link, target

    def test_substituted_cleanup_link_is_left_untouched_and_cannot_grant_replacement(self):
        from modlab.validation import windows_junction as junction
        store, run_id, fixture, link, target = self.failed_fixture()
        link.rmdir()
        junction.create_mod_projection(target, link)
        observed = link.lstat().st_ino
        with self.assertRaises(service.ContainmentServiceError):
            service.recover_preparation(store.root, run_id)
        self.assertEqual(observed, link.lstat().st_ino)
        self.assertFalse(store.preparation_path(run_id, "recovery").exists())
        self.assertEqual(b"never change source payload", (target / "marker.txt").read_bytes())

    def test_cross_reference_refusal_does_not_publish_an_immutable_poison_record(self):
        from modlab.validation import mo2_preparation_recovery as records
        store, run_id, *_ = self.failed_fixture()
        failure = store.load_preparation_failure(run_id)
        invalid = records.PreparationRecovery(run_id, failure.intent_id, failure.command_fingerprint,
            "preparation-failure-sha256:" + "0" * 64, records.native_preparation_process_inventory(),
            (), (), (), True)
        with self.assertRaises(ContainmentStoreError):
            store.write_preparation_recovery(invalid)
        self.assertFalse(store.preparation_path(run_id, "recovery").exists())

    def test_legacy_failure_cannot_reinterpret_an_existing_new_format_attempt(self):
        from dataclasses import replace
        store, run_id, *_ = self.failed_fixture()
        failure = store.load_preparation_failure(run_id)
        # Keep the test-created original failure rather than overwrite its bytes.
        store.preparation_path(run_id, "failure").rename(self.root / "retained-original-failure.json")
        invalid = replace(failure, attempt_id=None, projection_ids=(), legacy=True)
        with self.assertRaises(ContainmentStoreError):
            store.write_preparation_failure(invalid)
        self.assertFalse(store.preparation_path(run_id, "failure").exists())

    def test_exact_consumed_preparation_predecessor_does_not_require_fake_scenario_history(self):
        from tests.test_mo2_containment_service import write_cohort_run
        from modlab.validation.mo2_containment_model import CapabilityVerdict
        store, run_id, *_ = self.failed_fixture()
        recovery = service.recover_preparation(store.root, run_id).value
        fresh_id = "containment-run:" + "1" * 32
        store.consume_preparation_replacement(recovery, fresh_id)
        intent = store.load_intent(run_id)
        write_cohort_run(store, fresh_id, Path(intent["sourceWorkspace"]), Path(intent["steamRoot"]),
            intent["mo2ArtifactId"], predecessors=(run_id,))
        fresh_intent = store.load_intent(fresh_id)
        store.write_preparation_attempt(service._new_preparation_attempt(fresh_id,
            service._intent_id_for(fresh_intent), intent["commandFingerprint"], recovery))
        decision = service.adjudicate_run(store.root, fresh_id).value
        self.assertEqual(CapabilityVerdict.SUPPORTED, decision.verdict, decision.reasons)
        self.assertFalse(store.request_path(run_id).exists())
        self.assertEqual((), tuple((store.run_path(run_id) / "scenarios").iterdir()))

    def test_unknown_link_blocks_cleanup_before_any_owned_link_moves(self):
        from modlab.validation import windows_junction as junction
        store, run_id, fixture, link, target = self.failed_fixture()
        unknown = link.parent / "Unknown"
        junction.create_mod_projection(target, unknown)
        with self.assertRaises(service.ContainmentServiceError):
            service.recover_preparation(store.root, run_id)
        self.assertTrue(link.is_junction())
        self.assertTrue(unknown.is_junction())
        self.assertFalse(store.preparation_path(run_id, "recovery").exists())

    def test_interrupted_cleanup_never_grants_authority_without_exact_reconciliation(self):
        from modlab.validation import windows_junction as junction
        store, run_id, fixture, link, target = self.failed_fixture()
        real = junction._rename_pinned_object
        def interrupted(*args):
            real(*args)
            raise RuntimeError("interrupted after exact move")
        with patch.object(junction, "_rename_pinned_object", side_effect=interrupted):
            with self.assertRaises(service.ContainmentServiceError):
                service.recover_preparation(store.root, run_id)
        self.assertFalse(store.preparation_path(run_id, "recovery").exists())
        with self.assertRaises(service.ContainmentServiceError):
            service.restart_preparation(store.root, run_id)
        self.assertFalse(store.preparation_path(run_id, "replacement").exists())
        self.assertEqual(b"never change source payload", (target / "marker.txt").read_bytes())

    def test_concurrent_consumption_is_atomic_and_cross_command_replay_refuses(self):
        from dataclasses import replace
        store, run_id, *_ = self.failed_fixture()
        recovery = service.recover_preparation(store.root, run_id).value
        with self.assertRaises(ContainmentStoreError):
            store.consume_preparation_replacement(replace(recovery, command_fingerprint="containment-command-sha256:" + "0" * 64), "containment-run:" + "1" * 32)
        outcomes = []
        barrier = threading.Barrier(2)
        def consume(letter):
            barrier.wait()
            try:
                outcomes.append(store.consume_preparation_replacement(recovery, "containment-run:" + letter * 32))
            except ContainmentStoreError:
                outcomes.append(None)
        threads = [threading.Thread(target=consume, args=(letter,)) for letter in ("1", "2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, sum(item is not None for item in outcomes))

    def test_substituted_quarantined_link_cannot_consume_cached_recovery(self):
        from modlab.validation import windows_junction as junction
        store, run_id, fixture, link, target = self.failed_fixture()
        recovery = service.recover_preparation(store.root, run_id).value
        cleaned = Path(recovery.cleaned[0])
        cleaned.rmdir()
        junction.create_mod_projection(target, cleaned)
        with patch.object(service, "preflight_containment_fixture", side_effect=RuntimeError("must not enter replacement")):
            with self.assertRaises(service.ContainmentServiceError):
                service.restart_preparation(store.root, run_id)
        self.assertFalse(store.preparation_path(run_id, "replacement").exists())
        self.assertEqual((run_id,), store.list_run_ids())

    def test_cached_recovery_holds_all_cleanup_identities_while_reproving_absence(self):
        store, run_id, fixture, link, target = self.failed_fixture()
        recovery = service.recover_preparation(store.root, run_id).value
        cleaned = Path(recovery.cleaned[0])
        checked = []
        real = service._preparation_absence_proof
        def prove(attempt):
            for path in (cleaned, cleaned.parent, target, target.parent, link.parent):
                with self.assertRaises(OSError):
                    path.rename(path.with_name(path.name + "-substitution"))
                checked.append(path)
            return real(attempt)
        with patch.object(service, "_preparation_absence_proof", side_effect=prove):
            self.assertEqual(recovery, service.recover_preparation(store.root, run_id).value)
        self.assertEqual(5, len(checked))

    def test_legacy_live_process_refusal_does_not_record_an_abandonment(self):
        store, run_id, *_ = self.failed_fixture(legacy=True)
        with patch.object(service, "native_preparation_process_inventory", side_effect=RuntimeError("incomplete native inventory")):
            with self.assertRaises(service.ContainmentServiceError):
                service.recover_preparation(store.root, run_id)
        self.assertFalse(store.preparation_path(run_id, "failure").exists())

    def test_started_scenario_child_is_not_empty_direct_scaffolding(self):
        store, run_id, *_ = self.failed_fixture()
        (store.run_path(run_id) / "scenarios/NewFolder").mkdir()
        with self.assertRaises(service.ContainmentServiceError):
            service.recover_preparation(store.root, run_id)
        self.assertFalse(store.preparation_path(run_id, "recovery").exists())

    def test_failed_replacement_has_its_own_lineage_without_refunding_old_authority(self):
        store, run_id, *_ = self.failed_fixture()
        with patch.object(service, "preflight_containment_fixture", side_effect=RuntimeError("fresh failure")):
            with self.assertRaises(service.ContainmentServiceError) as raised:
                service.restart_preparation(store.root, run_id)
        replacement = store.load_preparation_replacement(run_id)
        self.assertNotEqual(run_id, replacement.consumed_by_run_id)
        fresh = store.load_preparation_attempt(replacement.consumed_by_run_id)
        self.assertEqual(replacement.recovery_id, fresh.replaces_recovery_id)
        store.load_preparation_failure(fresh.run_id)
        self.assertIn(store.run_path(run_id) / "preparation-quarantine-NewFolder", raised.exception.effects.child_mutation_roots)
        with self.assertRaises(service.ContainmentServiceError):
            service.restart_preparation(store.root, run_id)

    def test_fresh_projection_receipt_parent_failure_is_a_durable_new_attempt_failure(self):
        store, run_id, *_ = self.failed_fixture()
        service.recover_preparation(store.root, run_id)
        real = service._delegated_mutations_with_created_root
        def fail_receipt_parent(root, *args, **kwargs):
            if Path(root).name == "preparation-projections":
                raise RuntimeError("fresh projection receipt parent refused")
            return real(root, *args, **kwargs)
        with patch.object(service, "_delegated_mutations_with_created_root", side_effect=fail_receipt_parent):
            with self.assertRaises(service.ContainmentServiceError):
                service.restart_preparation(store.root, run_id)
        replacement = store.load_preparation_replacement(run_id)
        fresh = store.load_preparation_attempt(replacement.consumed_by_run_id)
        self.assertTrue(store.preparation_path(fresh.run_id, "failure").exists())
        failure = store.load_preparation_failure(fresh.run_id)
        self.assertIn("receipt parent refused", failure.reason)

    def test_legacy_restart_consumes_only_new_authority_and_preserves_old_fixture(self):
        store, run_id, fixture, link, target = self.failed_fixture(legacy=True)
        old_bytes = store.intent_path(run_id).read_bytes()
        old_link_id = link.lstat().st_ino
        with patch.object(service, "preflight_containment_fixture", side_effect=RuntimeError("fresh preflight deliberately fails")):
            with self.assertRaises(service.ContainmentServiceError):
                service.restart_preparation(store.root, run_id)
        replacement = store.load_preparation_replacement(run_id)
        fresh = store.load_preparation_attempt(replacement.consumed_by_run_id)
        self.assertEqual(replacement.recovery_id, fresh.replaces_recovery_id)
        self.assertEqual(old_bytes, store.intent_path(run_id).read_bytes())
        self.assertEqual(old_link_id, link.lstat().st_ino)
        self.assertFalse(store.preparation_path(run_id, "attempt").exists())
        self.assertEqual(b"never change source payload", (target / "marker.txt").read_bytes())

    def test_legacy_exact_intent_only_disposition_preserves_every_unproved_fixture(self):
        store, run_id, fixture, link, target = self.failed_fixture(legacy=True)
        old_intent = store.intent_path(run_id).read_bytes()
        link_identity = link.lstat().st_ino
        receipt = service.recover_preparation(store.root, run_id)
        self.assertTrue(receipt.value.fresh_run_permitted)
        self.assertEqual((), receipt.value.cleaned)
        self.assertEqual(link_identity, link.lstat().st_ino)
        self.assertEqual(old_intent, store.intent_path(run_id).read_bytes())
        self.assertEqual(b"never change source payload", (target / "marker.txt").read_bytes())
        self.assertFalse(store.request_path(run_id).exists())
        self.assertFalse(store.preparation_path(run_id, "attempt").exists())

    def test_unknown_legacy_shape_and_prepared_run_refuse_without_disposition(self):
        store, run_id, *_ = self.failed_fixture(legacy=True)
        (store.run_path(run_id) / "unexplained-owner.json").write_bytes(b"unknown")
        with self.assertRaises(service.ContainmentServiceError):
            service.recover_preparation(store.root, run_id)
        self.assertFalse(store.preparation_path(run_id, "failure").exists())
        store.write_request(run_id, {"runId": run_id})
        with self.assertRaises(service.ContainmentServiceError):
            service.recover_preparation(store.root, run_id)
