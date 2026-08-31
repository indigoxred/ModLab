import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapReceiptMode,
    ProcessIdentity,
    ProcessObservation,
)
from modlab.adapters.mo2.bootstrap_serialization import (
    journal_to_bytes,
    receipt_id_for,
)
from modlab.workflows.skyrim import mo2_bootstrap
from modlab.workflows.skyrim.mo2_bootstrap import (
    Mo2BootstrapRefusal,
    apply_mo2_setup,
    recover_mo2_setup,
)
from modlab.workflows.skyrim.mo2_bootstrap_store import (
    Mo2BootstrapStore,
    Mo2BootstrapStoreError,
)
from tests.support.mo2_bootstrap import BootstrapPlanningFixture, tree_state


JOB_ID = "bootstrap-job:fedcba9876543210fedcba9876543210"


class Mo2BootstrapRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = BootstrapPlanningFixture(Path(self.temporary.name))
        self.plan = self.fixture.prepare().plan
        self.before_target = tree_state(self.fixture.layout.skyrim_mo2)
        self.before_external = self.fixture.external_state()

    def _apply(self):
        self.fixture.runner.calls.clear()
        arguments = {
            "release_path": self.fixture.release_path,
            "extractor_path": Path(r"C:\Windows\System32\tar.exe"),
            "documents_root": self.fixture.documents_root,
            "version_reader": self.fixture.version_reader,
            "process_inspector": lambda _: self.fixture.processes,
            "command_runner": self.fixture.runner,
            "free_space_reader": lambda _: self.fixture.free_bytes,
            "clock": lambda: datetime(2026, 8, 31, 1, tzinfo=timezone.utc),
            "job_id_factory": lambda: JOB_ID,
        }
        self.fixture.runner.responses.setdefault(
            "-xf",
            self.fixture.runner.responses["-tf"].__class__((), 0, b"", b""),
        )
        self.fixture.runner.extraction_files = dict(self.fixture.package_files)
        with patch.object(
            mo2_bootstrap,
            "load_mo2_release",
            return_value=self.fixture.release,
        ):
            return apply_mo2_setup(
                self.plan.plan_id,
                self.fixture.workspace,
                **arguments,
            )

    def _recover(self, **overrides):
        self.fixture.runner.calls.clear()
        arguments = {
            "release_path": self.fixture.release_path,
            "extractor_path": Path(r"C:\Windows\System32\tar.exe"),
            "documents_root": self.fixture.documents_root,
            "version_reader": self.fixture.version_reader,
            "process_inspector": lambda _: self.fixture.processes,
            "command_runner": self.fixture.runner,
            "clock": lambda: datetime(2026, 8, 31, 2, tzinfo=timezone.utc),
        }
        arguments.update(overrides)
        with patch.object(
            mo2_bootstrap,
            "load_mo2_release",
            return_value=self.fixture.release,
        ):
            return recover_mo2_setup(JOB_ID, self.fixture.workspace, **arguments)

    def _job(self):
        return Mo2BootstrapStore(self.fixture.workspace).load_job(JOB_ID).journal

    def _interrupt_activated(self):
        with patch.object(
            Mo2BootstrapStore,
            "write_receipt",
            side_effect=KeyboardInterrupt("fixture crash before receipt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()
        self.assertEqual(BootstrapJobState.ACTIVATED, self._job().state)

    def _interrupt_applying(self):
        replace_path = mo2_bootstrap._replace_path

        def crash_activation(source, target):
            if Path(source).name.startswith(".skyrim-se-ae.modlab-stage-"):
                raise KeyboardInterrupt("fixture crash during activation")
            return replace_path(source, target)

        with patch.object(
            mo2_bootstrap,
            "_replace_path",
            side_effect=crash_activation,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()
        self.assertEqual(BootstrapJobState.APPLYING, self._job().state)

    def _interrupt_staged(self):
        transition = Mo2BootstrapStore.transition_job

        def crash_before_applying(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.APPLYING:
                raise KeyboardInterrupt("fixture crash before Applying")
            return transition(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=crash_before_applying,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()
        self.assertEqual(BootstrapJobState.STAGED, self._job().state)

    def _interrupt_partial_staging(self):
        def crash_with_partial_stage(*arguments, **_kwargs):
            stage_app = Path(arguments[3])
            stage_app.mkdir()
            (stage_app / "partial.bin").write_bytes(b"unrecorded partial extraction")
            raise KeyboardInterrupt("fixture crash during extraction")

        with patch.object(
            mo2_bootstrap,
            "extract_and_inventory",
            side_effect=crash_with_partial_stage,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()
        self.assertEqual(BootstrapJobState.STAGING, self._job().state)

    def _interrupt_planned(self):
        transition = Mo2BootstrapStore.transition_job

        def crash_before_staging(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.STAGING:
                raise KeyboardInterrupt("fixture crash before Staging")
            return transition(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=crash_before_staging,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()
        self.assertEqual(BootstrapJobState.PLANNED, self._job().state)

    def test_recovery_service_is_publicly_exported(self):
        self.assertIn("recover_mo2_setup", mo2_bootstrap.__all__)

    def test_activated_valid_target_is_finalized(self):
        self._interrupt_activated()
        activated_target = tree_state(self.fixture.layout.skyrim_mo2)

        result = self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertIsNotNone(result.receipt)
        self.assertEqual(activated_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(self.before_external, self.fixture.external_state())
        self.assertEqual(("portable-mo2-create",), result.installations)
        self.assertEqual(
            ["--version", "-tf", "-tvf"],
            [arguments[1] for arguments in self.fixture.runner.calls],
        )
        self.assertTrue(all(not Path(path).is_absolute() for path in result.paths_written))
        self.assertFalse(Path(result.journal.prior_root).exists())

    def test_applying_without_activation_restores_exact_empty_target(self):
        self._interrupt_applying()
        job = self._job()
        self.assertFalse(self.fixture.layout.skyrim_mo2.exists())
        self.assertTrue(Path(job.prior_root).exists())
        self.assertTrue(Path(job.stage_root).exists())

        result = self._recover()

        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(self.before_external, self.fixture.external_state())
        self.assertFalse(Path(job.prior_root).exists())
        self.assertFalse(Path(job.stage_root).exists())
        self.assertTrue(
            (Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
             / "recovered-stage").exists()
        )
        self.assertEqual([], self.fixture.runner.calls)

    def test_unexpected_nonempty_target_remains_recovery_required(self):
        self._interrupt_applying()
        final_root = self.fixture.layout.skyrim_mo2
        final_root.mkdir()
        unknown = final_root / "unknown.txt"
        unknown.write_bytes(b"mine")

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "unexpected") as raised:
            self._recover()

        self.assertEqual("recovery-required", raised.exception.code)
        self.assertEqual(b"mine", unknown.read_bytes())
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)
        self.assertEqual(self.before_external, self.fixture.external_state())

    def test_staged_job_quarantines_only_the_exact_recorded_stage(self):
        self._interrupt_staged()
        job = self._job()
        stage_state = tree_state(Path(job.stage_root))

        result = self._recover()

        quarantine = (
            Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
            / "recovered-stage"
        )
        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertFalse(Path(job.stage_root).exists())
        self.assertEqual(stage_state, tree_state(quarantine))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        repeated = self._recover()
        self.assertEqual(BootstrapJobState.RECOVERED, repeated.journal.state)
        self.assertEqual((), repeated.paths_written)

    def test_modified_staged_tree_is_left_untouched(self):
        self._interrupt_staged()
        job = self._job()
        changed = Path(job.stage_root) / "app" / "resources" / "base.dat"
        changed.write_bytes(b"not the recorded stage")
        changed_stage = tree_state(Path(job.stage_root))

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "stage"):
            self._recover()

        self.assertEqual(changed_stage, tree_state(Path(job.stage_root)))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_running_process_blocks_recovery_before_manager_changes(self):
        self._interrupt_applying()
        job = self._job()
        before_stage = tree_state(Path(job.stage_root))
        before_prior = tree_state(Path(job.prior_root))
        running = ProcessObservation(
            complete=True,
            relevant=(
                ProcessIdentity(
                    pid=91,
                    image_name="ModOrganizer.exe",
                    executable_path=str(
                        self.fixture.layout.skyrim_mo2_app / "ModOrganizer.exe"
                    ),
                ),
            ),
            error=None,
        )

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "running"):
            self._recover(process_inspector=lambda _: running)

        self.assertFalse(self.fixture.layout.skyrim_mo2.exists())
        self.assertEqual(before_stage, tree_state(Path(job.stage_root)))
        self.assertEqual(before_prior, tree_state(Path(job.prior_root)))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_receipt_failure_is_retryable_from_recovery_required(self):
        self._interrupt_activated()
        target = tree_state(self.fixture.layout.skyrim_mo2)

        with patch.object(
            Mo2BootstrapStore,
            "write_receipt",
            side_effect=Mo2BootstrapStoreError("fixture receipt failure"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "receipt"):
                self._recover()

        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)
        self.assertEqual(target, tree_state(self.fixture.layout.skyrim_mo2))

        result = self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertIsNotNone(result.receipt)
        self.assertEqual(target, tree_state(self.fixture.layout.skyrim_mo2))

    def test_planned_job_does_not_claim_an_unexpected_stage_path(self):
        self._interrupt_planned()
        stage = Path(self._job().stage_root)
        stage.mkdir()
        unknown = stage / "unknown.txt"
        unknown.write_bytes(b"not owned by a Planned job")
        before_stage = tree_state(stage)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "unverifiable"):
            self._recover()

        self.assertEqual(before_stage, tree_state(stage))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.PLANNED, self._job().state)

    def test_partial_staging_without_inventory_is_left_untouched(self):
        self._interrupt_partial_staging()
        stage = Path(self._job().stage_root)
        before_stage = tree_state(stage)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "unverifiable"):
            self._recover()

        self.assertEqual(before_stage, tree_state(stage))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_modified_activated_target_is_left_untouched(self):
        self._interrupt_activated()
        changed = (
            self.fixture.layout.skyrim_mo2_app / "resources" / "base.dat"
        )
        changed.write_bytes(b"unknown activated bytes")
        changed_target = tree_state(self.fixture.layout.skyrim_mo2)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "unexpected"):
            self._recover()

        self.assertEqual(changed_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_unrecorded_empty_directory_blocks_activated_finalization(self):
        self._interrupt_activated()
        extra = self.fixture.layout.skyrim_mo2 / "unexpected-empty-directory"
        extra.mkdir()
        changed_target = tree_state(self.fixture.layout.skyrim_mo2)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "exact"):
            self._recover()

        self.assertEqual(changed_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_missing_prior_blocks_applying_recovery_without_moving_stage(self):
        self._interrupt_applying()
        job = self._job()
        missing_prior = Path(job.prior_root).with_name("fixture-missing-prior")
        Path(job.prior_root).replace(missing_prior)
        stage_state = tree_state(Path(job.stage_root))

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "prior"):
            self._recover()

        self.assertFalse(self.fixture.layout.skyrim_mo2.exists())
        self.assertEqual(stage_state, tree_state(Path(job.stage_root)))
        self.assertEqual(self.before_target, tree_state(missing_prior))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_restore_rename_failure_is_retryable_from_filesystem_evidence(self):
        self._interrupt_applying()
        job = self._job()
        replace_path = mo2_bootstrap._replace_path

        def fail_restore(source, target):
            if Path(source) == Path(job.prior_root):
                raise OSError("fixture restore rename failure")
            return replace_path(source, target)

        with patch.object(
            mo2_bootstrap,
            "_replace_path",
            side_effect=fail_restore,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "rename failure"):
                self._recover()

        self.assertFalse(self.fixture.layout.skyrim_mo2.exists())
        self.assertEqual(self.before_target, tree_state(Path(job.prior_root)))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

        result = self._recover()

        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))

    def test_orphan_receipt_is_reused_after_verified_journal_failure(self):
        self._interrupt_activated()
        transition = Mo2BootstrapStore.transition_job

        def fail_verified(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.VERIFIED:
                raise Mo2BootstrapStoreError("fixture Verified transition failure")
            return transition(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=fail_verified,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "Verified"):
                self._recover()

        receipt_directory = self.fixture.layout.mo2_bootstrap_receipts
        receipts = tuple(receipt_directory.glob("*.json"))
        self.assertEqual(1, len(receipts))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

        result = self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertEqual(1, len(tuple(receipt_directory.glob("*.json"))))
        self.assertEqual(result.receipt.receipt_id, self._job().receipt_id)

    def test_exact_but_unready_activation_is_quarantined_and_prior_restored(self):
        self._interrupt_activated()
        activated = tree_state(self.fixture.layout.skyrim_mo2)

        with patch.object(
            mo2_bootstrap,
            "_observe_existing_manager",
            return_value=None,
        ):
            result = self._recover()

        quarantine = (
            Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
            / "recovered-activated"
        )
        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertEqual(activated, tree_state(quarantine))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(self.before_external, self.fixture.external_state())

    def test_cleanup_failure_leaves_verified_receipt_and_is_retryable(self):
        self._interrupt_activated()

        with patch.object(
            mo2_bootstrap,
            "_remove_preserved_empty_prior",
            side_effect=OSError("fixture cleanup failure"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "cleanup failure"):
                self._recover()

        verified = self._job()
        self.assertEqual(BootstrapJobState.VERIFIED, verified.state)
        self.assertIsNotNone(verified.receipt_id)
        self.assertTrue(Path(verified.prior_root).exists())

        result = self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertFalse(Path(verified.prior_root).exists())
        self.assertEqual((), result.programs_launched)

    def test_partial_known_prior_cleanup_resumes_after_restart(self):
        self._interrupt_activated()
        prior_root = Path(self._job().prior_root)
        real_rmdir = Path.rmdir
        child_removals = 0

        def fail_after_one_child(path):
            nonlocal child_removals
            current = Path(path)
            if current.parent == prior_root:
                child_removals += 1
                if child_removals == 2:
                    raise OSError("fixture mid-cleanup failure")
            return real_rmdir(current)

        with patch.object(
            Path,
            "rmdir",
            autospec=True,
            side_effect=fail_after_one_child,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "mid-cleanup"):
                self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, self._job().state)
        self.assertTrue(prior_root.exists())
        self.assertEqual(4, len(tree_state(prior_root)))

        result = self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertFalse(prior_root.exists())

    def test_changed_journal_is_rejected_without_moving_any_tree(self):
        self._interrupt_staged()
        store = Mo2BootstrapStore(self.fixture.workspace)
        journal_path = store.journal_path(JOB_ID)
        stage = store.stage_root(JOB_ID)
        stage_state = tree_state(stage)
        journal_path.write_bytes(b"changed journal")

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "journal"):
            self._recover()

        self.assertEqual(b"changed journal", journal_path.read_bytes())
        self.assertEqual(stage_state, tree_state(stage))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))

    def test_changed_verified_receipt_is_rejected(self):
        result = self._apply()
        receipt_path = Mo2BootstrapStore(self.fixture.workspace).receipt_path(
            result.receipt.receipt_id
        )
        receipt_path.write_bytes(b"changed receipt")

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "receipt"):
            self._recover()

        self.assertEqual(b"changed receipt", receipt_path.read_bytes())
        self.assertEqual(BootstrapJobState.VERIFIED, self._job().state)

    def test_verified_journal_rejects_canonical_receipt_with_wrong_mode(self):
        result = self._apply()
        store = Mo2BootstrapStore(self.fixture.workspace)
        draft = replace(
            result.receipt,
            receipt_id="bootstrap-receipt-sha256:" + "0" * 64,
            mode=BootstrapReceiptMode.ADOPTED,
        )
        incompatible = replace(draft, receipt_id=receipt_id_for(draft))
        store.write_receipt(incompatible)
        journal = replace(result.journal, receipt_id=incompatible.receipt_id)
        store.journal_path(JOB_ID).write_bytes(journal_to_bytes(journal))
        target = tree_state(self.fixture.layout.skyrim_mo2)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "receipt") as raised:
            self._recover()

        self.assertEqual("receipt-invalid", raised.exception.code)
        self.assertEqual(target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.VERIFIED, self._job().state)

    def test_active_target_with_recreated_stage_collision_is_untouched(self):
        self._interrupt_activated()
        target = tree_state(self.fixture.layout.skyrim_mo2)
        stage = Path(self._job().stage_root)
        stage.mkdir()
        (stage / "unknown.txt").write_bytes(b"collision")
        stage_state = tree_state(stage)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "stage"):
            self._recover()

        self.assertEqual(target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(stage_state, tree_state(stage))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_crash_after_activation_rename_is_finalized_from_applying_evidence(self):
        replace_path = mo2_bootstrap._replace_path

        def activate_then_crash(source, target):
            result = replace_path(source, target)
            if Path(source).name.startswith(".skyrim-se-ae.modlab-stage-"):
                raise KeyboardInterrupt("fixture crash after activation rename")
            return result

        with patch.object(
            mo2_bootstrap,
            "_replace_path",
            side_effect=activate_then_crash,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()

        applying = self._job()
        self.assertEqual(BootstrapJobState.APPLYING, applying.state)
        self.assertTrue(self.fixture.layout.skyrim_mo2_app.exists())
        self.assertFalse(Path(applying.stage_root).exists())

        result = self._recover()

        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertIsNotNone(result.receipt)

    def test_stage_quarantine_survives_journal_failure_and_retries(self):
        self._interrupt_staged()
        job = self._job()
        stage_state = tree_state(Path(job.stage_root))
        transition = Mo2BootstrapStore.transition_job

        def fail_recovery_write(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.RECOVERY_REQUIRED:
                raise Mo2BootstrapStoreError("fixture recovery journal failure")
            return transition(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=fail_recovery_write,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "journal failure"):
                self._recover()

        quarantine = (
            Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
            / "recovered-stage"
        )
        self.assertFalse(Path(job.stage_root).exists())
        self.assertEqual(stage_state, tree_state(quarantine))
        self.assertEqual(BootstrapJobState.STAGED, self._job().state)

        result = self._recover()

        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertEqual(stage_state, tree_state(quarantine))

    def test_target_change_during_stage_quarantine_is_not_marked_recovered(self):
        self._interrupt_staged()
        job = self._job()
        relocate = mo2_bootstrap._relocate_recovery_tree

        def mutate_target(source, target, label, **kwargs):
            result = relocate(source, target, label, **kwargs)
            if label == "bootstrap stage":
                (self.fixture.layout.skyrim_mo2 / "unknown.txt").write_bytes(
                    b"appeared during quarantine"
                )
            return result

        with patch.object(
            mo2_bootstrap,
            "_relocate_recovery_tree",
            side_effect=mutate_target,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "target change"):
                self._recover()

        quarantine = (
            Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
            / "recovered-stage"
        )
        self.assertFalse(Path(job.stage_root).exists())
        self.assertTrue(quarantine.exists())
        self.assertEqual(
            b"appeared during quarantine",
            (self.fixture.layout.skyrim_mo2 / "unknown.txt").read_bytes(),
        )
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_manager_ancestry_change_after_active_observation_blocks_finalization(self):
        self._interrupt_activated()
        target = tree_state(self.fixture.layout.skyrim_mo2)
        observe = mo2_bootstrap._active_recovery_snapshot
        validate = mo2_bootstrap._validate_captured_ancestry
        armed = False

        def observe_then_arm(*arguments, **kwargs):
            nonlocal armed
            result = observe(*arguments, **kwargs)
            armed = True
            return result

        def reject_changed_ancestry(ancestry):
            if armed:
                raise mo2_bootstrap.Mo2ArchiveError(
                    "simulated manager ancestry swap"
                )
            return validate(ancestry)

        with patch.object(
            mo2_bootstrap,
            "_active_recovery_snapshot",
            side_effect=observe_then_arm,
        ), patch.object(
            mo2_bootstrap,
            "_validate_captured_ancestry",
            side_effect=reject_changed_ancestry,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "ancestry swap"):
                self._recover()

        self.assertEqual(target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_manager_ancestry_change_after_stage_quarantine_blocks_recovered(self):
        self._interrupt_staged()
        job = self._job()
        stage = Path(job.stage_root)
        stage_state = tree_state(stage)
        relocate = mo2_bootstrap._relocate_recovery_tree
        validate = mo2_bootstrap._validate_captured_ancestry
        armed = False

        def relocate_then_arm(*arguments, **kwargs):
            nonlocal armed
            result = relocate(*arguments, **kwargs)
            if arguments[2] == "bootstrap stage":
                armed = True
            return result

        def reject_changed_ancestry(ancestry):
            if armed:
                raise mo2_bootstrap.Mo2ArchiveError(
                    "simulated manager ancestry swap"
                )
            return validate(ancestry)

        with patch.object(
            mo2_bootstrap,
            "_relocate_recovery_tree",
            side_effect=relocate_then_arm,
        ), patch.object(
            mo2_bootstrap,
            "_validate_captured_ancestry",
            side_effect=reject_changed_ancestry,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "ancestry swap"):
                self._recover()

        quarantine = (
            Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
            / "recovered-stage"
        )
        self.assertFalse(stage.exists())
        self.assertEqual(stage_state, tree_state(quarantine))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_changed_plan_is_rejected_without_moving_any_tree(self):
        self._interrupt_staged()
        store = Mo2BootstrapStore(self.fixture.workspace)
        plan_path = store.plan_path(self.plan.plan_id)
        stage = store.stage_root(JOB_ID)
        stage_state = tree_state(stage)
        plan_path.write_bytes(b"changed plan")

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery evidence"):
            self._recover()

        self.assertEqual(b"changed plan", plan_path.read_bytes())
        self.assertEqual(stage_state, tree_state(stage))
        self.assertEqual(self.before_target, tree_state(self.fixture.layout.skyrim_mo2))

    def test_adopt_recovery_quarantines_stage_without_changing_manager(self):
        self.fixture.make_ready_existing()
        self.plan = self.fixture.prepare().plan
        before_manager = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()

        with patch.object(
            Mo2BootstrapStore,
            "write_receipt",
            side_effect=KeyboardInterrupt("fixture Adopt crash before receipt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()

        staged = self._job()
        self.assertEqual(BootstrapJobState.STAGED, staged.state)
        stage_state = tree_state(Path(staged.stage_root))

        result = self._recover()

        quarantine = (
            Mo2BootstrapStore(self.fixture.workspace).job_directory(JOB_ID)
            / "recovered-stage"
        )
        self.assertEqual(BootstrapJobState.RECOVERED, result.journal.state)
        self.assertEqual(stage_state, tree_state(quarantine))
        self.assertEqual(before_manager, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self.assertEqual([], self.fixture.runner.calls)
        replanned = self.fixture.prepare()
        self.assertEqual(BootstrapDisposition.ADOPT, replanned.plan.disposition)

    def test_recovered_adopt_orphan_receipt_does_not_poison_replanning(self):
        self.fixture.make_ready_existing()
        self.plan = self.fixture.prepare().plan
        transition = Mo2BootstrapStore.transition_job

        def crash_after_adopt_receipt(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.VERIFIED:
                raise KeyboardInterrupt("fixture crash after Adopt receipt")
            return transition(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=crash_after_adopt_receipt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply()

        receipt_files = tuple(self.fixture.layout.mo2_bootstrap_receipts.glob("*.json"))
        self.assertEqual(1, len(receipt_files))
        self.assertEqual(BootstrapJobState.STAGED, self._job().state)

        recovered = self._recover()

        self.assertEqual(BootstrapJobState.RECOVERED, recovered.journal.state)
        replanned = self.fixture.prepare()
        self.assertEqual(BootstrapDisposition.ADOPT, replanned.plan.disposition)


if __name__ == "__main__":
    unittest.main()
