import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from modlab.platform import windows_exact_fs
from modlab.platform.windows_exact_fs import RetainedObjectRole
from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapReceiptMode,
    FileIdentity,
    TargetSnapshot,
)
from modlab.adapters.mo2.bootstrap_serialization import (
    journal_to_bytes,
    plan_to_bytes,
    receipt_to_bytes,
)
from modlab.workflows.skyrim.mo2_bootstrap_store import (
    BootstrapReceiptMatch,
    Mo2BootstrapNotFoundError,
    Mo2BootstrapStore,
    Mo2BootstrapStoreError,
    Mo2BootstrapStoreOwnershipError,
    Mo2BootstrapStorePromotionError,
)
from modlab.workspace import initialize_workspace
from tests.support.mo2_bootstrap import (
    make_plan_fixture,
    make_receipt_fixture,
)


JOB_HEX = "0123456789abcdef0123456789abcdef"
JOB_ID = "bootstrap-job:" + JOB_HEX


class Mo2BootstrapStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name, "workspace")
        self.layout = initialize_workspace(self.workspace)
        self.clock_value = datetime(2026, 8, 31, 1, 2, 3, tzinfo=timezone.utc)
        self.store = Mo2BootstrapStore(
            self.workspace,
            clock=lambda: self.clock_value,
            job_id_factory=lambda: JOB_ID,
        )
        self.plan = self._plan()

    def _plan(
        self,
        *,
        disposition: BootstrapDisposition = BootstrapDisposition.CREATE,
        target_kind: str = "Empty",
    ):
        target = TargetSnapshot(
            kind=target_kind,
            root=str(self.layout.skyrim_mo2),
            inventory_sha256="8" * 64,
            entry_count=0 if target_kind == "Empty" else 7,
        )
        return make_plan_fixture(
            disposition=disposition,
            workspace_root=str(self.layout.root),
            final_root=str(self.layout.skyrim_mo2),
            staging_parent=str(self.layout.skyrim_mo2.parent),
            target=target,
        )

    def _receipt(self, plan=None, **overrides):
        plan = plan or self.plan
        final_root = str(self.layout.skyrim_mo2)
        values = {
            "plan_id": plan.plan_id,
            "job_id": JOB_ID,
            "mode": (
                BootstrapReceiptMode.ADOPTED
                if plan.disposition is BootstrapDisposition.ADOPT
                else BootstrapReceiptMode.CREATED
            ),
            "release_id": plan.release_id,
            "release_descriptor_sha256": plan.release_descriptor_sha256,
            "archive_artifact_id": plan.archive.artifact_id,
            "archive_metadata_sha256": plan.archive.metadata_sha256,
            "archive_sha256": plan.archive.sha256,
            "archive_size": plan.archive.size,
            "skyrim_executable": plan.skyrim_executable,
            "mo2_executable": FileIdentity(
                path=str(self.layout.skyrim_mo2_app / "ModOrganizer.exe"),
                sha256="7" * 64,
                size=5028352,
            ),
            "final_root": final_root,
            "downloads_root": str(self.layout.skyrim_mo2_downloads),
            "mods_root": str(self.layout.skyrim_mo2_mods),
            "profiles_root": str(self.layout.skyrim_mo2_profiles),
            "overwrite_root": str(self.layout.skyrim_mo2_overwrite),
            "webcache_root": str(self.layout.skyrim_mo2 / "webcache"),
        }
        values.update(overrides)
        return make_receipt_fixture(**values)

    @staticmethod
    def _next_journal(stored, state, **overrides):
        values = {
            "state": state,
            "updated_at": "2026-08-31T01:02:04Z",
        }
        values.update(overrides)
        return replace(stored.journal, **values)

    def _create_job(self, plan=None):
        plan = plan or self.plan
        self.store.write_plan(plan)
        return self.store.create_job(plan)

    def _activate_create_job(self):
        planned = self._create_job()
        staging = self.store.transition_job(
            planned,
            BootstrapJobState.PLANNED,
            self._next_journal(planned, BootstrapJobState.STAGING),
        )
        staged = self.store.transition_job(
            staging,
            BootstrapJobState.STAGING,
            self._next_journal(
                staging,
                BootstrapJobState.STAGED,
                stage_inventory_sha256="e" * 64,
                stage_entry_count=1626,
            ),
        )
        applying = self.store.transition_job(
            staged,
            BootstrapJobState.STAGED,
            self._next_journal(staged, BootstrapJobState.APPLYING),
        )
        return self.store.transition_job(
            applying,
            BootstrapJobState.APPLYING,
            self._next_journal(
                applying,
                BootstrapJobState.ACTIVATED,
                activated_inventory_sha256="f" * 64,
                activated_entry_count=1648,
            ),
        )

    def _verify_create_receipt(self):
        activated = self._activate_create_job()
        stored_receipt = self.store.write_receipt(self._receipt())
        verified = self.store.transition_job(
            activated,
            BootstrapJobState.ACTIVATED,
            self._next_journal(
                activated,
                BootstrapJobState.VERIFIED,
                receipt_id=stored_receipt.receipt.receipt_id,
            ),
        )
        return stored_receipt, verified

    def test_plan_is_content_addressed_idempotent_and_conflict_safe(self):
        with patch.object(
            windows_exact_fs,
            "publish_new_pinned",
            wraps=windows_exact_fs.publish_new_pinned,
        ) as publish:
            stored = self.store.write_plan(self.plan)
        repeated = self.store.write_plan(self.plan)

        self.assertTrue(stored.changed)
        self.assertFalse(repeated.changed)
        self.assertEqual(plan_to_bytes(self.plan), stored.path.read_bytes())
        if os.name == "nt":
            self.assertEqual(1, publish.call_count)
        stored.path.write_bytes(b"changed")
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "different bytes"):
            self.store.write_plan(self.plan)
        self.assertEqual(b"changed", stored.path.read_bytes())

    def test_load_plan_requires_exact_canonical_bytes_and_matching_filename(self):
        stored = self.store.write_plan(self.plan)
        loaded = self.store.load_plan(self.plan.plan_id)
        self.assertEqual(self.plan, loaded.plan)
        self.assertEqual(plan_to_bytes(self.plan), loaded.data)
        self.assertEqual(stored.sha256, loaded.sha256)

        stored.path.write_bytes(b" " + plan_to_bytes(self.plan))
        with self.assertRaises(Mo2BootstrapStoreError):
            self.store.load_plan(self.plan.plan_id)

    def test_missing_or_invalid_plan_identity_never_becomes_a_path(self):
        with self.assertRaises(Mo2BootstrapNotFoundError):
            self.store.load_plan("bootstrap-plan-sha256:" + "f" * 64)
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "plan ID"):
            self.store.plan_path("../journal.json")

    def test_create_job_derives_confined_paths_and_durably_records_planned(self):
        stored = self._create_job()

        self.assertEqual(JOB_ID, stored.journal.job_id)
        self.assertEqual(BootstrapJobState.PLANNED, stored.journal.state)
        self.assertEqual(self.plan.plan_id, stored.journal.plan_id)
        self.assertEqual(str(self.store.stage_root(JOB_ID)), stored.journal.stage_root)
        self.assertEqual(str(self.store.prior_root(JOB_ID)), stored.journal.prior_root)
        self.assertEqual(str(self.layout.skyrim_mo2), stored.journal.final_root)
        self.assertEqual(stored.path, self.store.journal_path(JOB_ID))
        self.assertEqual(journal_to_bytes(stored.journal), stored.path.read_bytes())
        self.assertFalse(self.store.stage_root(JOB_ID).exists())
        self.assertFalse(self.store.prior_root(JOB_ID).exists())

    def test_create_job_requires_exact_stored_plan_and_usable_disposition(self):
        with self.assertRaisesRegex(Mo2BootstrapNotFoundError, "plan"):
            self.store.create_job(self.plan)

        blocked = self._plan(disposition=BootstrapDisposition.BLOCKED)
        self.store.write_plan(blocked)
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "Create or Adopt"):
            self.store.create_job(blocked)

    def test_plan_for_another_workspace_is_never_stored(self):
        foreign = make_plan_fixture()

        with self.assertRaisesRegex(Mo2BootstrapStoreError, "workspace root"):
            self.store.write_plan(foreign)

        self.assertEqual([], list(self.layout.mo2_bootstrap_plans.glob("*.json")))

    def test_create_job_records_existing_prior_snapshot_for_adopt(self):
        plan = self._plan(
            disposition=BootstrapDisposition.ADOPT,
            target_kind="Existing",
        )

        stored = self._create_job(plan)

        self.assertEqual("Existing", stored.journal.prior_target_kind)
        self.assertEqual(plan.target.inventory_sha256, stored.journal.prior_inventory_sha256)
        self.assertEqual(plan.target.entry_count, stored.journal.prior_entry_count)

    def test_duplicate_job_identity_is_never_reused(self):
        self._create_job()

        with self.assertRaisesRegex(Mo2BootstrapStoreError, "already exists"):
            self.store.create_job(self.plan)

    def test_transition_is_compare_and_swap_on_exact_observed_bytes_and_state(self):
        planned = self._create_job()
        staging = self._next_journal(planned, BootstrapJobState.STAGING)

        advanced = self.store.transition_job(
            planned,
            BootstrapJobState.PLANNED,
            staging,
        )

        self.assertEqual(BootstrapJobState.STAGING, advanced.journal.state)
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "changed after observation"):
            self.store.transition_job(
                planned,
                BootstrapJobState.PLANNED,
                staging,
            )
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "expected state"):
            self.store.transition_job(
                advanced,
                BootstrapJobState.PLANNED,
                self._next_journal(advanced, BootstrapJobState.STAGED),
            )

    def test_transition_rejects_external_byte_change_and_identity_change(self):
        planned = self._create_job()
        planned.path.write_bytes(b"modified")
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "changed after observation"):
            self.store.transition_job(
                planned,
                BootstrapJobState.PLANNED,
                self._next_journal(planned, BootstrapJobState.STAGING),
            )

        planned.path.write_bytes(planned.data)
        changed_identity = self._next_journal(
            planned,
            BootstrapJobState.STAGING,
            plan_id="bootstrap-plan-sha256:" + "f" * 64,
        )
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "immutable journal"):
            self.store.transition_job(
                planned,
                BootstrapJobState.PLANNED,
                changed_identity,
            )

    def test_create_and_adopt_state_machines_allow_only_declared_edges(self):
        create = self._create_job()
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "transition"):
            self.store.transition_job(
                create,
                BootstrapJobState.PLANNED,
                self._next_journal(
                    create,
                    BootstrapJobState.RECOVERED,
                ),
            )

        staging = self.store.transition_job(
            create,
            BootstrapJobState.PLANNED,
            self._next_journal(create, BootstrapJobState.STAGING),
        )
        failed = self.store.transition_job(
            staging,
            BootstrapJobState.STAGING,
            self._next_journal(
                staging,
                BootstrapJobState.RECOVERY_REQUIRED,
                error="extraction interrupted",
            ),
        )
        recovered = self.store.transition_job(
            failed,
            BootstrapJobState.RECOVERY_REQUIRED,
            self._next_journal(
                failed,
                BootstrapJobState.RECOVERED,
                error=None,
            ),
        )
        self.assertEqual(BootstrapJobState.RECOVERED, recovered.journal.state)

    def test_recovery_required_create_can_reestablish_activated_then_verified(self):
        planned = self._create_job()
        failed = self.store.transition_job(
            planned,
            BootstrapJobState.PLANNED,
            self._next_journal(
                planned,
                BootstrapJobState.RECOVERY_REQUIRED,
                error="activation outcome needs re-evaluation",
            ),
        )
        activated = self.store.transition_job(
            failed,
            BootstrapJobState.RECOVERY_REQUIRED,
            self._next_journal(
                failed,
                BootstrapJobState.ACTIVATED,
                stage_inventory_sha256="e" * 64,
                stage_entry_count=1626,
                activated_inventory_sha256="f" * 64,
                activated_entry_count=1648,
                error=None,
            ),
        )
        receipt = self.store.write_receipt(self._receipt()).receipt
        verified = self.store.transition_job(
            activated,
            BootstrapJobState.ACTIVATED,
            self._next_journal(
                activated,
                BootstrapJobState.VERIFIED,
                receipt_id=receipt.receipt_id,
            ),
        )

        self.assertEqual(BootstrapJobState.VERIFIED, verified.journal.state)

    def test_adopt_can_verify_without_apply_or_activation_states(self):
        plan = self._plan(
            disposition=BootstrapDisposition.ADOPT,
            target_kind="Existing",
        )
        planned = self._create_job(plan)
        staging = self.store.transition_job(
            planned,
            BootstrapJobState.PLANNED,
            self._next_journal(planned, BootstrapJobState.STAGING),
        )
        staged = self.store.transition_job(
            staging,
            BootstrapJobState.STAGING,
            self._next_journal(
                staging,
                BootstrapJobState.STAGED,
                stage_inventory_sha256="e" * 64,
                stage_entry_count=1626,
            ),
        )
        receipt = self.store.write_receipt(self._receipt(plan)).receipt
        verified = self.store.transition_job(
            staged,
            BootstrapJobState.STAGED,
            self._next_journal(
                staged,
                BootstrapJobState.VERIFIED,
                receipt_id=receipt.receipt_id,
            ),
        )
        self.assertEqual(BootstrapJobState.VERIFIED, verified.journal.state)

    def test_create_can_advance_through_apply_and_activation_only_with_receipt(self):
        _, verified = self._verify_create_receipt()

        self.assertEqual(BootstrapJobState.VERIFIED, verified.journal.state)

    def test_receipt_is_content_addressed_immutable_and_strictly_loaded(self):
        receipt = self._receipt()

        first = self.store.write_receipt(receipt)
        repeated = self.store.write_receipt(receipt)

        self.assertTrue(first.changed)
        self.assertFalse(repeated.changed)
        self.assertEqual(receipt_to_bytes(receipt), first.path.read_bytes())
        self.assertEqual(receipt, self.store.load_receipt(receipt.receipt_id).receipt)
        first.path.write_bytes(b"changed")
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "different bytes"):
            self.store.write_receipt(receipt)

    def test_verified_transition_rejects_receipt_that_conflicts_with_plan(self):
        activated = self._activate_create_job()
        receipt = self.store.write_receipt(
            self._receipt(archive_metadata_sha256="f" * 64)
        ).receipt

        with self.assertRaisesRegex(Mo2BootstrapStoreError, "retained plan"):
            self.store.transition_job(
                activated,
                BootstrapJobState.ACTIVATED,
                self._next_journal(
                    activated,
                    BootstrapJobState.VERIFIED,
                    receipt_id=receipt.receipt_id,
                ),
            )

        self.assertEqual(
            BootstrapJobState.ACTIVATED,
            self.store.load_job(JOB_ID).journal.state,
        )

    def test_find_compatible_receipt_filters_verified_evidence_and_never_hides_damage(self):
        stored, _ = self._verify_create_receipt()
        receipt = stored.receipt
        match = BootstrapReceiptMatch.from_plan(self.plan)

        found = self.store.find_compatible_receipt(match)

        self.assertIsNotNone(found)
        self.assertEqual(receipt.receipt_id, found.receipt.receipt_id)
        self.assertIsNone(
            self.store.find_compatible_receipt(
                replace(match, archive_sha256="f" * 64)
            )
        )

        malformed = self.layout.mo2_bootstrap_receipts / ("f" * 64 + ".json")
        malformed.write_bytes(b"{}")
        with self.assertRaisesRegex(Mo2BootstrapStoreError, "receipt"):
            self.store.find_compatible_receipt(match)
        self.assertEqual(receipt_to_bytes(receipt), stored.path.read_bytes())

    def test_matching_receipt_without_verified_journal_requires_recovery(self):
        receipt = self._receipt()
        self.store.write_receipt(receipt)

        with self.assertRaisesRegex(Mo2BootstrapStoreError, "retained plan and job"):
            self.store.find_compatible_receipt(
                BootstrapReceiptMatch.from_plan(self.plan)
            )

    def test_pre_promotion_failure_leaves_no_target_or_part_file(self):
        target = self.store.plan_path(self.plan.plan_id)
        with patch.object(
            windows_exact_fs,
            "write_pinned_file",
            side_effect=OSError("write failed"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapStoreError, "write failed"):
                self.store.write_plan(self.plan)

        self.assertFalse(target.exists())
        self.assertEqual([], list(self.layout.mo2_bootstrap_jobs.glob("*.part")))
        self.assertEqual([], list(target.parent.glob(f".{target.name}.*.tmp")))

    @unittest.skipUnless(os.name == "nt", "exact publication requires Windows")
    def test_private_candidate_ownership_reports_no_final_publication_effect(self):
        target = self.store.plan_path(self.plan.plan_id)
        real_delete = windows_exact_fs.delete_pinned_object

        def retain_private_candidate(candidate):
            if candidate.path.parent == target.parent and candidate.path.name.startswith(
                f".{target.name}."
            ):
                raise OSError("fixture retains private publication candidate")
            return real_delete(candidate)

        with patch.object(
            windows_exact_fs,
            "write_pinned_file",
            side_effect=OSError("fixture pre-rename write failure"),
        ), patch.object(
            windows_exact_fs,
            "delete_pinned_object",
            side_effect=retain_private_candidate,
        ):
            with self.assertRaises(Mo2BootstrapStoreOwnershipError) as raised:
                self.store.write_plan(self.plan)

        error = raised.exception
        self.assertFalse(error.changed)
        publication = getattr(error, "publication", None)
        self.assertIsNotNone(publication)
        self.assertEqual("private-candidate", publication.phase.value)
        self.assertEqual("no-destination-change", publication.effect.value)
        self.assertFalse(publication.completed)
        self.assertFalse(target.exists())
        self.assertEqual(1, len(error.candidates))
        self.assertNotEqual(0, error.candidate.handle)

        error.resolve()

        self.assertFalse(target.exists())
        self.assertEqual([], list(target.parent.glob(f".{target.name}.*.tmp")))

    def test_initial_journal_validation_failure_preserves_promotion_evidence(self):
        self.store.write_plan(self.plan)
        target = self.store.journal_path(JOB_ID)
        validate = Mo2BootstrapStore._validate_existing_file

        def fail_promoted_target(store, path, label):
            if path == target and target.exists():
                raise Mo2BootstrapStoreError(
                    "fixture target validation failed after promotion"
                )
            return validate(store, path, label)

        with patch.object(
            Mo2BootstrapStore,
            "_validate_existing_file",
            new=fail_promoted_target,
        ):
            with self.assertRaises(Mo2BootstrapStorePromotionError) as raised:
                self.store.create_job(self.plan)

        self.assertEqual("journal", raised.exception.record_kind)
        self.assertEqual(JOB_ID, raised.exception.record_id)
        self.assertEqual(target, raised.exception.path)
        self.assertTrue(raised.exception.changed)

    def test_receipt_read_failure_preserves_promotion_evidence(self):
        receipt = self._receipt()
        target = self.store.receipt_path(receipt.receipt_id)
        read = Mo2BootstrapStore._read_existing_bytes

        def fail_promoted_target(path, label):
            if path == target and target.exists():
                raise Mo2BootstrapStoreError(
                    "fixture target read failed after promotion"
                )
            return read(path, label)

        with patch.object(
            Mo2BootstrapStore,
            "_read_existing_bytes",
            new=staticmethod(fail_promoted_target),
        ):
            with self.assertRaises(Mo2BootstrapStorePromotionError) as raised:
                self.store.write_receipt(receipt)

        self.assertEqual("receipt", raised.exception.record_kind)
        self.assertEqual(receipt.receipt_id, raised.exception.record_id)
        self.assertEqual(target, raised.exception.path)
        self.assertTrue(raised.exception.changed)

    def test_post_promotion_disappearance_preserves_record_identity(self):
        self.store.write_plan(self.plan)
        with patch.object(
            self.store,
            "load_job",
            side_effect=Mo2BootstrapNotFoundError("fixture journal disappeared"),
        ):
            with self.assertRaises(Mo2BootstrapStorePromotionError) as journal_error:
                self.store.create_job(self.plan)

        receipt = self._receipt()
        with patch.object(
            self.store,
            "load_receipt",
            side_effect=Mo2BootstrapNotFoundError("fixture receipt disappeared"),
        ):
            with self.assertRaises(Mo2BootstrapStorePromotionError) as receipt_error:
                self.store.write_receipt(receipt)

        self.assertEqual("journal", journal_error.exception.record_kind)
        self.assertEqual(JOB_ID, journal_error.exception.record_id)
        self.assertTrue(journal_error.exception.changed)
        self.assertEqual("receipt", receipt_error.exception.record_kind)
        self.assertEqual(receipt.receipt_id, receipt_error.exception.record_id)
        self.assertTrue(receipt_error.exception.changed)

    def test_immutable_race_never_overwrites_bytes_that_appear_at_target(self):
        target = self.store.plan_path(self.plan.plan_id)
        real_publish = windows_exact_fs.publish_new_pinned

        def competing_publication(destination, data, validator):
            Path(destination).write_bytes(b"racing writer")
            return real_publish(destination, data, validator)

        with patch.object(
            windows_exact_fs,
            "publish_new_pinned",
            side_effect=competing_publication,
        ):
            with self.assertRaisesRegex(Mo2BootstrapStoreError, "different bytes"):
                self.store.write_plan(self.plan)

        self.assertEqual(b"racing writer", target.read_bytes())

    def test_concurrent_stale_journal_writer_cannot_promote(self):
        planned = self._create_job()
        first = self._next_journal(
            planned,
            BootstrapJobState.STAGING,
            updated_at="2026-08-31T01:02:04Z",
        )
        second = self._next_journal(
            planned,
            BootstrapJobState.STAGING,
            updated_at="2026-08-31T01:02:05Z",
        )
        entered = threading.Event()
        release = threading.Event()
        real_replace = os.replace
        replacement_calls = 0
        call_guard = threading.Lock()

        def controlled_replace(source, target):
            nonlocal replacement_calls
            with call_guard:
                replacement_calls += 1
                call_number = replacement_calls
            if call_number == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release first writer")
            return real_replace(source, target)

        with patch(
            "modlab.workflows.skyrim.mo2_bootstrap_store.os.replace",
            side_effect=controlled_replace,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self.store.transition_job,
                    planned,
                    BootstrapJobState.PLANNED,
                    first,
                )
                self.assertTrue(entered.wait(5))
                try:
                    with self.assertRaisesRegex(
                        Mo2BootstrapStoreError,
                        "already being changed",
                    ):
                        self.store.transition_job(
                            planned,
                            BootstrapJobState.PLANNED,
                            second,
                        )
                finally:
                    release.set()
                advanced = future.result(timeout=5)

        self.assertEqual(1, replacement_calls)
        self.assertEqual(first, advanced.journal)
        self.assertEqual(first, self.store.load_job(JOB_ID).journal)

    def test_promotion_failure_preserves_old_journal_and_cleans_part(self):
        planned = self._create_job()
        with patch(
            "modlab.workflows.skyrim.mo2_bootstrap_store.os.replace",
            side_effect=OSError("promotion failed"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapStoreError, "promotion failed"):
                self.store.transition_job(
                    planned,
                    BootstrapJobState.PLANNED,
                    self._next_journal(planned, BootstrapJobState.STAGING),
                )

        self.assertEqual(planned.data, planned.path.read_bytes())
        self.assertEqual([], list(self.layout.mo2_bootstrap_jobs.glob("*.part")))

    def test_failed_initial_journal_promotion_removes_only_its_new_empty_job(self):
        self.store.write_plan(self.plan)
        with patch.object(
            windows_exact_fs,
            "publish_new_pinned",
            side_effect=OSError("promotion failed"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapStoreError, "promotion failed"):
                self.store.create_job(self.plan)

        self.assertFalse(self.store.job_directory(JOB_ID).exists())
        self.assertFalse(self.store.stage_root(JOB_ID).exists())
        self.assertEqual([], list(self.layout.mo2_bootstrap_jobs.glob("*.part")))

    @unittest.skipUnless(os.name == "nt", "exact publication requires Windows")
    def test_initial_journal_ownership_resolution_unwinds_exact_job_then_retries(self):
        self.store.write_plan(self.plan)
        job_directory = self.store.job_directory(JOB_ID)
        journal = self.store.journal_path(JOB_ID)
        unrelated = self.layout.mo2_bootstrap_jobs / "unrelated.keep"
        unrelated.write_bytes(b"unrelated bytes")
        real_write = windows_exact_fs.write_pinned_file
        real_delete = windows_exact_fs.delete_pinned_object

        def fail_journal_write(candidate, data):
            if candidate.path.parent == job_directory:
                raise OSError("fixture initial journal write failure")
            return real_write(candidate, data)

        def retain_journal_candidate(candidate):
            if candidate.path.parent == job_directory and candidate.path.name.startswith(
                ".journal.json."
            ):
                raise OSError("fixture retains exact journal candidate")
            return real_delete(candidate)

        with patch.object(
            windows_exact_fs,
            "write_pinned_file",
            side_effect=fail_journal_write,
        ), patch.object(
            windows_exact_fs,
            "delete_pinned_object",
            side_effect=retain_journal_candidate,
        ):
            with self.assertRaises(Mo2BootstrapStoreOwnershipError) as raised:
                self.store.create_job(self.plan)

        error = raised.exception
        self.assertFalse(error.changed)
        self.assertEqual("private-candidate", error.publication.phase.value)
        self.assertEqual(
            "no-destination-change", error.publication.effect.value
        )
        self.assertFalse(journal.exists())
        self.assertTrue(job_directory.exists())
        self.assertEqual(
            {job_directory, error.candidate.path},
            {candidate.path for candidate in error.candidates},
        )
        self.assertEqual(b"unrelated bytes", unrelated.read_bytes())

        failed_job_cleanup = False

        def fail_exact_job_cleanup_once(candidate):
            nonlocal failed_job_cleanup
            if candidate.path == job_directory and not failed_job_cleanup:
                failed_job_cleanup = True
                raise OSError("fixture retains exact job directory once")
            return real_delete(candidate)

        with patch.object(
            windows_exact_fs,
            "delete_pinned_object",
            side_effect=fail_exact_job_cleanup_once,
        ):
            with self.assertRaises(Mo2BootstrapStoreOwnershipError) as unresolved:
                error.resolve()

        self.assertIs(error, unresolved.exception)
        self.assertEqual("private-candidate", error.publication.phase.value)
        self.assertEqual(
            "no-destination-change", error.publication.effect.value
        )
        self.assertTrue(failed_job_cleanup)
        self.assertFalse(journal.exists())
        self.assertTrue(job_directory.exists())
        self.assertEqual(
            (job_directory,),
            tuple(candidate.path for candidate in error.candidates),
        )
        self.assertEqual([], list(job_directory.iterdir()))
        self.assertEqual(b"unrelated bytes", unrelated.read_bytes())

        error.resolve()

        self.assertFalse(job_directory.exists())
        self.assertEqual(b"unrelated bytes", unrelated.read_bytes())
        retried = self.store.create_job(self.plan)
        self.assertEqual(BootstrapJobState.PLANNED, retried.journal.state)
        self.assertEqual(journal_to_bytes(retried.journal), journal.read_bytes())

    def test_completed_publication_later_reload_failure_keeps_document(self):
        target = self.store.plan_path(self.plan.plan_id)
        with patch.object(
            self.store,
            "load_plan",
            side_effect=Mo2BootstrapStoreError("fixture later reload failure"),
        ):
            with self.assertRaises(Mo2BootstrapStorePromotionError) as raised:
                self.store.write_plan(self.plan)

        self.assertTrue(raised.exception.changed)
        self.assertEqual(target, raised.exception.path)
        self.assertEqual(plan_to_bytes(self.plan), target.read_bytes())
        self.assertEqual([], list(target.parent.glob(f".{target.name}.*.tmp")))

    @unittest.skipUnless(os.name == "nt", "exact publication requires Windows")
    def test_immutable_owner_failure_keeps_record_identity_and_live_union(self):
        target = self.store.plan_path(self.plan.plan_id)
        rename = windows_exact_fs.rename_pinned_no_replace
        close = windows_exact_fs.PinnedObject.close
        fail_ids: set[int] = set()

        def arm_published_owners(source, destination, parent, **kwargs):
            result = rename(source, destination, parent, **kwargs)
            if Path(destination) == target:
                fail_ids.update((id(source), id(parent)))
            return result

        def fail_armed_close(owner):
            if id(owner) in fail_ids and owner.handle:
                raise OSError("fixture publication owner close failure")
            return close(owner)

        with patch.object(
            windows_exact_fs,
            "rename_pinned_no_replace",
            side_effect=arm_published_owners,
        ), patch.object(
            windows_exact_fs.PinnedObject,
            "close",
            autospec=True,
            side_effect=fail_armed_close,
        ):
            with self.assertRaises(Exception) as raised:
                self.store.write_plan(self.plan)

        error = raised.exception
        self.assertIsInstance(error, Mo2BootstrapStoreError)
        self.assertEqual("plan", error.record_kind)
        self.assertEqual(self.plan.plan_id, error.record_id)
        self.assertEqual(target, error.path)
        self.assertTrue(error.changed)
        publication = getattr(error, "publication", None)
        self.assertIsNotNone(publication)
        self.assertEqual("rename-visible", publication.phase.value)
        self.assertEqual("rollback-incomplete", publication.effect.value)
        self.assertFalse(publication.completed)
        self.assertEqual(
            {RetainedObjectRole.CANDIDATE, RetainedObjectRole.DESTINATION_PARENT},
            {owner.role for owner in error.owners},
        )
        error.resolve()
        self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "nt", "exact publication requires Windows")
    def test_renamed_then_rolled_back_publication_reports_visible_incomplete_effect(self):
        target = self.store.plan_path(self.plan.plan_id)
        rename = windows_exact_fs.rename_pinned_no_replace
        close = windows_exact_fs.PinnedObject.close
        parent_owner = None

        def arm_parent_after_rename(source, destination, parent, **kwargs):
            nonlocal parent_owner
            result = rename(source, destination, parent, **kwargs)
            if Path(destination) == target:
                parent_owner = parent
            return result

        def retain_only_parent(owner):
            if owner is parent_owner and owner.handle:
                raise OSError("fixture retains publication parent")
            return close(owner)

        with patch.object(
            windows_exact_fs,
            "rename_pinned_no_replace",
            side_effect=arm_parent_after_rename,
        ), patch.object(
            windows_exact_fs.PinnedObject,
            "close",
            autospec=True,
            side_effect=retain_only_parent,
        ):
            with self.assertRaises(Mo2BootstrapStoreOwnershipError) as raised:
                self.store.write_plan(self.plan)

        error = raised.exception
        self.assertTrue(error.changed)
        publication = getattr(error, "publication", None)
        self.assertIsNotNone(publication)
        self.assertEqual("rename-visible", publication.phase.value)
        self.assertEqual("rolled-back", publication.effect.value)
        self.assertFalse(publication.completed)
        self.assertFalse(target.exists())
        self.assertEqual(
            (RetainedObjectRole.DESTINATION_PARENT,),
            tuple(owner.role for owner in error.owners),
        )

        error.resolve()

    def test_redirected_plan_job_and_receipt_ancestors_are_rejected(self):
        cases = (
            (self.layout.mo2_bootstrap_plans, lambda: self.store.write_plan(self.plan)),
            (
                self.store.job_directory(JOB_ID),
                lambda: self._create_job(),
            ),
            (
                self.layout.mo2_bootstrap_receipts,
                lambda: self.store.write_receipt(self._receipt()),
            ),
        )
        real_is_symlink = Path.is_symlink
        for redirected, action in cases:
            with self.subTest(path=redirected):
                redirected.mkdir(parents=True, exist_ok=True)

                def report_redirect(path):
                    return Path(path) == redirected or real_is_symlink(path)

                with patch.object(
                    Path,
                    "is_symlink",
                    autospec=True,
                    side_effect=report_redirect,
                ):
                    with self.assertRaisesRegex(Mo2BootstrapStoreError, "redirected"):
                        action()


if __name__ == "__main__":
    unittest.main()
