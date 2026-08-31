import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapReceiptMode,
    ProcessIdentity,
    ProcessObservation,
)
from modlab.adapters.mo2.archive import Mo2ArchiveError, inventory_tree
from modlab.adapters.mo2.projection import Mo2Readiness, project_mo2_state
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.workflows.skyrim.mo2_bootstrap import (
    Mo2BootstrapRefusal,
    apply_mo2_setup,
)
from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore
from tests.support.mo2_bootstrap import BootstrapPlanningFixture, tree_state


class Mo2BootstrapCreateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = BootstrapPlanningFixture(Path(self.temporary.name))

    def _plan_create(self):
        planned = self.fixture.prepare()
        self.assertEqual(BootstrapDisposition.CREATE, planned.plan.disposition)
        return planned.plan

    def _apply(self, plan_id: str, **overrides):
        runner = overrides.pop("command_runner", self.fixture.runner)
        runner.calls.clear()
        runner.responses.setdefault("-xf", CompletedProcess((), 0, b"", b""))
        if not runner.extraction_files:
            runner.extraction_files = dict(self.fixture.package_files)
        arguments = {
            "release_path": self.fixture.release_path,
            "extractor_path": Path(r"C:\Windows\System32\tar.exe"),
            "documents_root": self.fixture.documents_root,
            "version_reader": self.fixture.version_reader,
            "process_inspector": lambda _: self.fixture.processes,
            "command_runner": runner,
            "free_space_reader": lambda _: self.fixture.free_bytes,
            "clock": lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
            "job_id_factory": lambda: (
                "bootstrap-job:fedcba9876543210fedcba9876543210"
            ),
        }
        arguments.update(overrides)
        with patch(
            "modlab.workflows.skyrim.mo2_bootstrap.load_mo2_release",
            return_value=self.fixture.release,
        ):
            return apply_mo2_setup(plan_id, self.fixture.workspace, **arguments)

    def _job(self):
        return Mo2BootstrapStore(self.fixture.workspace).load_job(
            "bootstrap-job:fedcba9876543210fedcba9876543210"
        ).journal

    def _assert_recovery_required(self):
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_create_activates_exact_contained_ready_instance(self):
        plan = self._plan_create()
        before_external = self.fixture.external_state()

        result = self._apply(plan.plan_id)

        projection = project_mo2_state(
            inspect_skyrim_mo2(
                self.fixture.layout.skyrim_mo2_app,
                self.fixture.game_root,
                workspace_root=self.fixture.workspace,
                version_reader=self.fixture.version_reader,
            )
        )
        self.assertEqual(Mo2Readiness.READY, projection.readiness)
        self.assertEqual(BootstrapReceiptMode.CREATED, result.receipt.mode)
        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertEqual((), projection.adapter_state.installed_mods)
        self.assertEqual((), projection.adapter_state.overwrite_entries)
        self.assertFalse(projection.adapter_state.lab.profile_local_saves)
        lab_files = tuple(
            (PurePosixPath(item.relative_path).name, item.sha256, item.size)
            for item in projection.adapter_state.lab.state_files
        )
        play_files = tuple(
            (PurePosixPath(item.relative_path).name, item.sha256, item.size)
            for item in projection.adapter_state.play.state_files
        )
        self.assertEqual(lab_files, play_files)
        self.assertEqual(before_external, self.fixture.external_state())
        self.assertEqual(
            ["--version", "-tf", "-tvf", "-xf"],
            [arguments[1] for arguments in self.fixture.runner.calls],
        )
        self.assertEqual(
            [],
            list(self.fixture.layout.skyrim_mo2.parent.glob(
                ".skyrim-se-ae.modlab-stage-*"
            )),
        )
        expected_manager_changes = tuple(
            f"tools/mo2/skyrim-se-ae/{item.relative_path}"
            for item in inventory_tree(self.fixture.layout.skyrim_mo2).files
        )
        self.assertEqual(("portable-mo2-create",), result.installations)
        self.assertEqual(expected_manager_changes, result.manager_changes)
        self.assertTrue(
            all(not Path(path).is_absolute() for path in result.paths_written)
        )

    def test_absent_target_is_created_without_inventing_a_prior(self):
        for child in sorted(
            self.fixture.layout.skyrim_mo2.iterdir(),
            key=lambda path: path.name,
            reverse=True,
        ):
            child.rmdir()
        self.fixture.layout.skyrim_mo2.rmdir()
        plan = self._plan_create()

        result = self._apply(plan.plan_id)

        self.assertEqual(BootstrapReceiptMode.CREATED, result.receipt.mode)
        self.assertFalse(Path(result.journal.prior_root).exists())

    def test_target_changed_after_plan_refuses_before_job(self):
        plan = self._plan_create()
        (self.fixture.layout.skyrim_mo2 / "unknown.txt").write_bytes(b"user-owned")
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()

        with self.assertRaises(Mo2BootstrapRefusal):
            self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self.assertFalse(
            Mo2BootstrapStore(self.fixture.workspace)
            .job_directory("bootstrap-job:fedcba9876543210fedcba9876543210")
            .exists()
        )

    def test_stage_collision_refuses_without_changing_target(self):
        plan = self._plan_create()
        store = Mo2BootstrapStore(self.fixture.workspace)
        collision = store.stage_root(
            "bootstrap-job:fedcba9876543210fedcba9876543210"
        )
        collision.mkdir()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()

        with self.assertRaises(Mo2BootstrapRefusal):
            self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())

    def test_insufficient_staging_space_preserves_exact_target(self):
        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        reads = 0

        def space_reader(_):
            nonlocal reads
            reads += 1
            return self.fixture.free_bytes if reads == 1 else 1

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
            self._apply(plan.plan_id, free_space_reader=space_reader)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_extraction_failure_preserves_exact_target(self):
        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        self.fixture.runner.responses["-xf"] = CompletedProcess(
            (), 9, b"", b"fixture extraction failure"
        )

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
            self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_archive_change_during_extraction_preserves_exact_target(self):
        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        archive_hash = self.fixture.artifact_id.removeprefix("archive-sha256:")
        payload = (
            self.fixture.workspace
            / "library"
            / "archives"
            / archive_hash[:2]
            / archive_hash
            / "payload.7z"
        )
        run = self.fixture.runner.run

        def mutate_after_extract(arguments):
            result = run(arguments)
            if arguments[1] == "-xf":
                payload.write_bytes(b"changed during extraction")
            return result

        with patch.object(
            self.fixture.runner,
            "run",
            side_effect=mutate_after_extract,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "archive"):
                self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_generated_config_collision_preserves_exact_target(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        write_configuration = mo2_bootstrap.write_staged_configuration

        def collide(stage_root, manager_ini, profile_files):
            (Path(stage_root) / "app" / "ModOrganizer.ini").write_bytes(
                b"collision"
            )
            return write_configuration(stage_root, manager_ini, profile_files)

        with patch.object(
            mo2_bootstrap,
            "write_staged_configuration",
            side_effect=collide,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_profile_source_drift_during_render_preserves_exact_target(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        render_profiles = mo2_bootstrap.render_profile_files

        def drift(seed):
            source = Path(seed.ini_sources[0].path)
            original = source.read_bytes()
            source.write_bytes(b"changed during render")
            try:
                return render_profiles(seed)
            finally:
                source.write_bytes(original)

        with patch.object(
            mo2_bootstrap,
            "render_profile_files",
            side_effect=drift,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_failure_preserving_prior_leaves_exact_target(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        replace_path = mo2_bootstrap._replace_path

        def fail_preserve(source, target):
            if Path(source) == self.fixture.layout.skyrim_mo2:
                raise OSError("fixture preserve failure")
            return replace_path(source, target)

        with patch.object(mo2_bootstrap, "_replace_path", side_effect=fail_preserve):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_prior_appearing_during_preserve_rehash_is_not_overwritten(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        store = Mo2BootstrapStore(self.fixture.workspace)
        prior_root = store.prior_root(
            "bootstrap-job:fedcba9876543210fedcba9876543210"
        )
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        require_tree_identity = mo2_bootstrap._require_tree_identity
        injected = False

        def inject_prior(root, expected_root, expected_tree, label):
            nonlocal injected
            result = require_tree_identity(
                root,
                expected_root,
                expected_tree,
                label,
            )
            if label == "empty target" and not injected:
                injected = True
                prior_root.mkdir()
            return result

        with patch.object(
            mo2_bootstrap,
            "_require_tree_identity",
            side_effect=inject_prior,
        ), patch.object(
            mo2_bootstrap,
            "_replace_path",
            wraps=mo2_bootstrap._replace_path,
        ) as replace_path:
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self._apply(plan.plan_id)

        self.assertTrue(injected)
        self.assertEqual("target-changed", raised.exception.code)
        replace_path.assert_not_called()
        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual((), tree_state(prior_root))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_process_start_at_activation_boundary_preserves_exact_target(self):
        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        empty = ProcessObservation(complete=True, relevant=(), error=None)
        running = ProcessObservation(
            complete=True,
            relevant=(
                ProcessIdentity(
                    pid=73,
                    image_name="ModOrganizer.exe",
                    executable_path=str(
                        self.fixture.layout.skyrim_mo2_app / "ModOrganizer.exe"
                    ),
                ),
            ),
            error=None,
        )
        inspections = 0

        def starts_late(_):
            nonlocal inspections
            inspections += 1
            return running if inspections >= 5 else empty

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "running"):
            self._apply(plan.plan_id, process_inspector=starts_late)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_failure_activating_stage_restores_exact_target(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        replace_path = mo2_bootstrap._replace_path

        def fail_activation(source, target):
            if Path(source).name.startswith(".skyrim-se-ae.modlab-stage-"):
                raise OSError("fixture activation failure")
            return replace_path(source, target)

        with patch.object(mo2_bootstrap, "_replace_path", side_effect=fail_activation):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_target_appearing_after_stage_rehash_is_not_overwritten(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        require_tree_identity = mo2_bootstrap._require_tree_identity
        injected = False

        def inject_target(root, expected_root, expected_tree, label):
            nonlocal injected
            result = require_tree_identity(
                root,
                expected_root,
                expected_tree,
                label,
            )
            if label == "staged instance" and not injected:
                injected = True
                self.fixture.layout.skyrim_mo2.mkdir()
            return result

        with patch.object(
            mo2_bootstrap,
            "_require_tree_identity",
            side_effect=inject_target,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self._apply(plan.plan_id)

        job = self._job()
        self.assertTrue(injected)
        self.assertEqual("recovery-required", raised.exception.code)
        self.assertEqual((), tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_target, tree_state(Path(job.prior_root)))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_target_appearing_during_rollback_rehash_is_not_overwritten(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        require_tree_identity = mo2_bootstrap._require_tree_identity
        replace_path = mo2_bootstrap._replace_path
        preserved_rehashes = 0

        def inject_target(root, expected_root, expected_tree, label):
            nonlocal preserved_rehashes
            result = require_tree_identity(
                root,
                expected_root,
                expected_tree,
                label,
            )
            if label == "preserved target":
                preserved_rehashes += 1
                if preserved_rehashes == 3:
                    self.fixture.layout.skyrim_mo2.mkdir()
            return result

        def fail_activation(source, target):
            if Path(source).name.startswith(".skyrim-se-ae.modlab-stage-"):
                raise OSError("fixture activation failure")
            return replace_path(source, target)

        with patch.object(
            mo2_bootstrap,
            "_require_tree_identity",
            side_effect=inject_target,
        ), patch.object(
            mo2_bootstrap,
            "_replace_path",
            side_effect=fail_activation,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self._apply(plan.plan_id)

        job = self._job()
        self.assertEqual(3, preserved_rehashes)
        self.assertEqual("recovery-required", raised.exception.code)
        self.assertEqual((), tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_target, tree_state(Path(job.prior_root)))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_redirected_activation_ancestor_refuses_before_any_rename(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        validate_ancestry = mo2_bootstrap._validate_captured_ancestry
        rejected = False

        def reject_manager_ancestry(ancestry):
            nonlocal rejected
            if not rejected and any(
                path == self.fixture.layout.skyrim_mo2.parent
                for path, _ in ancestry
            ):
                rejected = True
                raise Mo2ArchiveError("simulated redirected activation ancestor")
            return validate_ancestry(ancestry)

        with patch.object(
            mo2_bootstrap,
            "_validate_captured_ancestry",
            side_effect=reject_manager_ancestry,
        ), patch.object(
            mo2_bootstrap,
            "_replace_path",
            wraps=mo2_bootstrap._replace_path,
        ) as replace_path:
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertTrue(rejected)
        replace_path.assert_not_called()
        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_post_activation_scanner_block_leaves_recovery_evidence(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_external = self.fixture.external_state()

        with patch.object(
            mo2_bootstrap,
            "_observe_existing_manager",
            return_value=None,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "Ready"):
                self._apply(plan.plan_id)

        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()
        self.assertTrue(self.fixture.layout.skyrim_mo2_app.exists())

    def test_receipt_failure_leaves_activated_recovery_evidence(self):
        plan = self._plan_create()
        before_external = self.fixture.external_state()

        with patch.object(
            Mo2BootstrapStore,
            "write_receipt",
            side_effect=OSError("fixture receipt failure"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()
        self.assertTrue(self.fixture.layout.skyrim_mo2_app.exists())

    def test_journal_failure_before_activation_preserves_exact_target(self):
        plan = self._plan_create()
        before_target = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()
        transition = Mo2BootstrapStore.transition_job

        def fail_staged(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.STAGED:
                raise OSError("fixture journal failure")
            return transition(store, observed, expected, replacement)

        with patch.object(Mo2BootstrapStore, "transition_job", new=fail_staged):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_target, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()

    def test_final_verification_byte_drift_requires_recovery(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_create()
        before_external = self.fixture.external_state()
        verify_created = mo2_bootstrap._verify_created_instance

        def drift(**arguments):
            (
                self.fixture.layout.skyrim_mo2_app
                / "resources"
                / "base.dat"
            ).write_bytes(b"changed before final verification")
            return verify_created(**arguments)

        with patch.object(
            mo2_bootstrap,
            "_verify_created_instance",
            side_effect=drift,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before_external, self.fixture.external_state())
        self._assert_recovery_required()


if __name__ == "__main__":
    unittest.main()
