import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapReceiptMode,
    ProcessIdentity,
    ProcessObservation,
)
from modlab.workflows.skyrim.mo2_bootstrap import (
    Mo2BootstrapRefusal,
    apply_mo2_setup,
)
from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore
from tests.support.mo2_bootstrap import BootstrapPlanningFixture, tree_state


class Mo2BootstrapAdoptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = BootstrapPlanningFixture(Path(self.temporary.name))
        self.fixture.make_ready_existing()

    def _plan_adopt(self):
        planned = self.fixture.prepare()
        self.assertEqual(BootstrapDisposition.ADOPT, planned.plan.disposition)
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
                "bootstrap-job:0123456789abcdef0123456789abcdef"
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
            "bootstrap-job:0123456789abcdef0123456789abcdef"
        ).journal

    def _assert_recovery_required(self):
        self.assertEqual(BootstrapJobState.RECOVERY_REQUIRED, self._job().state)

    def test_adopt_links_package_without_changing_existing_manager(self):
        plan = self._plan_adopt()
        before_manager = tree_state(self.fixture.layout.skyrim_mo2)
        before_external = self.fixture.external_state()

        result = self._apply(plan.plan_id)

        self.assertEqual(BootstrapReceiptMode.ADOPTED, result.receipt.mode)
        self.assertEqual(BootstrapJobState.VERIFIED, result.journal.state)
        self.assertEqual(before_manager, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(before_external, self.fixture.external_state())
        self.assertEqual(
            self.fixture.release.descriptor.package_file_count,
            result.receipt.package_file_count,
        )
        self.assertEqual([], list(self.fixture.layout.skyrim_mo2.parent.glob(
            ".s*"
        )))
        self.assertEqual((), result.manager_changes)
        self.assertEqual((), result.game_changes)
        self.assertEqual(
            {
                Mo2BootstrapStore(self.fixture.workspace)
                .journal_path(result.journal.job_id)
                .relative_to(self.fixture.workspace)
                .as_posix(),
                Mo2BootstrapStore(self.fixture.workspace)
                .receipt_path(result.receipt.receipt_id)
                .relative_to(self.fixture.workspace)
                .as_posix(),
            },
            set(result.paths_written),
        )
        self.assertEqual(
            ["--version", "-tf", "-tvf", "-tf", "-tvf", "-xf"],
            [arguments[1] for arguments in self.fixture.runner.calls],
        )

    def test_running_target_process_refuses_before_job_or_stage(self):
        running = ProcessObservation(
            complete=True,
            relevant=(
                ProcessIdentity(
                    pid=42,
                    image_name="ModOrganizer.exe",
                    executable_path=str(
                        self.fixture.layout.skyrim_mo2_app / "ModOrganizer.exe"
                    ),
                ),
            ),
            error=None,
        )
        self.fixture.processes = running
        plan = self._plan_adopt()
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "running"):
            self._apply(plan.plan_id)

        self.assertEqual(before, self.fixture.workspace_state())

    def test_planned_running_process_may_be_closed_before_apply(self):
        running = ProcessObservation(
            complete=True,
            relevant=(
                ProcessIdentity(
                    pid=42,
                    image_name="ModOrganizer.exe",
                    executable_path=str(
                        self.fixture.layout.skyrim_mo2_app / "ModOrganizer.exe"
                    ),
                ),
            ),
            error=None,
        )
        self.fixture.processes = running
        plan = self._plan_adopt()
        self.fixture.processes = ProcessObservation(
            complete=True,
            relevant=(),
            error=None,
        )
        before = tree_state(self.fixture.layout.skyrim_mo2)

        result = self._apply(plan.plan_id)

        self.assertEqual(BootstrapReceiptMode.ADOPTED, result.receipt.mode)
        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        process_finding = next(
            finding
            for finding in result.receipt.findings
            if finding.code == "mo2-process-running"
        )
        self.assertEqual("Passed", process_finding.state.value)

    def test_target_change_after_plan_refuses_before_job(self):
        plan = self._plan_adopt()
        (self.fixture.layout.skyrim_mo2 / "new-after-plan.txt").write_bytes(
            b"changed after planning"
        )
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "target"):
            self._apply(plan.plan_id)

        self.assertEqual(before, self.fixture.workspace_state())
        self.assertFalse(
            Mo2BootstrapStore(self.fixture.workspace)
            .job_directory("bootstrap-job:0123456789abcdef0123456789abcdef")
            .exists()
        )

    def test_archive_change_after_plan_refuses_before_job(self):
        plan = self._plan_adopt()
        archive_hash = self.fixture.artifact_id.removeprefix("archive-sha256:")
        payload = (
            self.fixture.workspace
            / "library"
            / "archives"
            / archive_hash[:2]
            / archive_hash
            / "payload.7z"
        )
        payload.write_bytes(b"changed after planning")
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "archive"):
            self._apply(plan.plan_id)

        self.assertEqual(before, self.fixture.workspace_state())
        self.assertFalse(
            Mo2BootstrapStore(self.fixture.workspace)
            .job_directory("bootstrap-job:0123456789abcdef0123456789abcdef")
            .exists()
        )

    def test_missing_package_file_blocks_before_apply(self):
        (self.fixture.layout.skyrim_mo2_app / "resources" / "base.dat").unlink()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        planned = self.fixture.prepare()

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapDisposition.BLOCKED, planned.plan.disposition)
        finding = next(
            item
            for item in planned.plan.findings
            if item.code == "existing-package-mismatch"
        )
        self.assertEqual("Blocked", finding.state.value)
        self.assertEqual(
            (),
            tuple(
                entry
                for entry in self.fixture.layout.mo2_bootstrap_jobs.iterdir()
                if entry.name != "plans"
            ),
        )

    def test_modified_package_file_is_refused_without_manager_writes(self):
        package_file = self.fixture.layout.skyrim_mo2_app / "resources" / "base.dat"
        package_file.write_bytes(b"locally modified package data")
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "modified"):
            self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self._assert_recovery_required()

    def test_redirected_package_file_refuses_before_job(self):
        package_file = self.fixture.layout.skyrim_mo2_app / "resources" / "base.dat"
        outside = self.fixture.root / "outside.dat"
        outside.write_bytes(b"outside")
        plan = self._plan_adopt()
        package_file.unlink()
        try:
            os.symlink(outside, package_file)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal):
            self._apply(plan.plan_id)

        self.assertEqual(before, self.fixture.workspace_state())

    def test_generated_nxmhandler_settings_are_recorded_and_adopted(self):
        settings = self.fixture.layout.skyrim_mo2_app / "nxmhandler.ini"
        settings.write_bytes(b"[General]\nnoregister=false\n")
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        result = self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertIn("nxmhandler.ini", result.receipt.extra_entries)

    def test_allowed_extra_casing_is_preserved_when_adopted(self):
        settings = self.fixture.layout.skyrim_mo2_app / "NxmHandler.ini"
        original = b"[General]\nnoregister=false\n"
        settings.write_bytes(original)

        plan = self._plan_adopt()
        result = self._apply(plan.plan_id)

        self.assertEqual(original, settings.read_bytes())
        self.assertIn("NxmHandler.ini", result.receipt.extra_entries)

    def test_allowed_root_log_is_recorded_and_adopted(self):
        log = self.fixture.layout.skyrim_mo2_app / "logs" / "session.log"
        log.parent.mkdir()
        log.write_bytes(b"allowed generated log")
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        result = self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertIn("logs/session.log", result.receipt.extra_entries)

    def test_allowed_python_bytecode_is_recorded_and_adopted(self):
        bytecode = (
            self.fixture.layout.skyrim_mo2_app
            / "plugins"
            / "__pycache__"
            / "generated.pyc"
        )
        bytecode.parent.mkdir()
        bytecode.write_bytes(b"allowed generated bytecode")
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        result = self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertIn("plugins/__pycache__/generated.pyc", result.receipt.extra_entries)

    def test_extractor_failure_preserves_manager_and_requires_recovery(self):
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)
        self.fixture.runner.responses["-xf"] = CompletedProcess(
            (), 7, b"", b"fixture extraction failure"
        )

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
            self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self._assert_recovery_required()

    def test_manager_mutation_observation_requires_recovery_without_writing_manager(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)
        original = mo2_bootstrap._snapshot_manager_tree
        final_calls = 0

        def changed_observation(root):
            nonlocal final_calls
            snapshot = original(root)
            if Path(root) == self.fixture.layout.skyrim_mo2:
                final_calls += 1
                if final_calls == 2:
                    return replace(
                        snapshot,
                        entries=(
                            *snapshot.entries,
                            replace(
                                snapshot.entries[0],
                                relative_path="external-change",
                            ),
                        ),
                    )
            return snapshot

        with patch.object(
            mo2_bootstrap,
            "_snapshot_manager_tree",
            side_effect=changed_observation,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "changed"):
                self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self._assert_recovery_required()

    def test_late_executable_version_mismatch_blocks_receipt(self):
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)
        mo2_reads = 0

        def changing_version(path):
            nonlocal mo2_reads
            if Path(path).name.casefold() == "modorganizer.exe":
                mo2_reads += 1
                return self.fixture.mo2_version if mo2_reads <= 2 else "9.9.9.9"
            return self.fixture.version_reader(path)

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "exact Ready"):
            self._apply(plan.plan_id, version_reader=changing_version)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self._assert_recovery_required()
        self.assertIsNone(self._job().receipt_id)

    def test_receipt_write_failure_preserves_manager_and_requires_recovery(self):
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        with patch.object(
            Mo2BootstrapStore,
            "write_receipt",
            side_effect=OSError("fixture receipt failure"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self._assert_recovery_required()

    def test_journal_failure_preserves_manager_and_requires_recovery(self):
        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)
        original = Mo2BootstrapStore.transition_job

        def fail_staged(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.STAGED:
                raise OSError("fixture journal failure")
            return original(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=fail_staged,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "recovery"):
                self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self._assert_recovery_required()

    def test_cleanup_failure_leaves_verified_receipt_and_manager_unchanged(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)

        with patch.object(
            mo2_bootstrap,
            "_remove_verified_stage",
            side_effect=OSError("fixture cleanup failure"),
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "not cleaned"):
                self._apply(plan.plan_id)

        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.VERIFIED, self._job().state)

    def test_cleanup_refuses_a_file_swapped_after_snapshot(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)
        original = mo2_bootstrap._validate_cleanup_entry
        swapped = False

        def swap_then_validate(stage_root, stage_identity, ancestry, entries, entry):
            nonlocal swapped
            if entry.relative_path == "resources/base.dat" and not swapped:
                swapped = True
                target = Path(stage_root) / "resources" / "base.dat"
                data = target.read_bytes()
                target.unlink()
                target.write_bytes(data)
            return original(stage_root, stage_identity, ancestry, entries, entry)

        with patch.object(
            mo2_bootstrap,
            "_validate_cleanup_entry",
            side_effect=swap_then_validate,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "not cleaned"):
                self._apply(plan.plan_id)

        self.assertTrue(swapped)
        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.VERIFIED, self._job().state)

    def test_cleanup_refuses_an_entry_ancestor_that_becomes_redirected(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        plan = self._plan_adopt()
        before = tree_state(self.fixture.layout.skyrim_mo2)
        validate = mo2_bootstrap._validate_cleanup_entry
        redirected = mo2_bootstrap._redirected
        simulated = False

        def validate_with_redirect(stage_root, stage_identity, ancestry, entries, entry):
            nonlocal simulated
            if entry.relative_path == "resources/base.dat" and not simulated:
                simulated = True

                def reports_redirect(path, metadata):
                    return Path(path).name == "resources" or redirected(path, metadata)

                with patch.object(
                    mo2_bootstrap,
                    "_redirected",
                    side_effect=reports_redirect,
                ):
                    return validate(
                        stage_root,
                        stage_identity,
                        ancestry,
                        entries,
                        entry,
                    )
            return validate(stage_root, stage_identity, ancestry, entries, entry)

        with patch.object(
            mo2_bootstrap,
            "_validate_cleanup_entry",
            side_effect=validate_with_redirect,
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "not cleaned"):
                self._apply(plan.plan_id)

        self.assertTrue(simulated)
        self.assertEqual(before, tree_state(self.fixture.layout.skyrim_mo2))
        self.assertEqual(BootstrapJobState.VERIFIED, self._job().state)

    def test_repeated_apply_returns_existing_receipt_without_new_job_or_extract(self):
        plan = self._plan_adopt()
        first = self._apply(plan.plan_id)
        before = self.fixture.workspace_state()

        second = self._apply(plan.plan_id)

        self.assertEqual(first.receipt, second.receipt)
        self.assertIsNone(second.journal)
        self.assertEqual((), second.paths_written)
        self.assertEqual(before, self.fixture.workspace_state())
        self.assertEqual(
            ["--version", "-tf", "-tvf"],
            [arguments[1] for arguments in self.fixture.runner.calls],
        )

    def test_preexisting_valid_receipt_is_a_write_free_noop(self):
        self.fixture.make_receipt_covered()
        plan = self.fixture.prepare().plan
        self.assertEqual(BootstrapDisposition.ALREADY_MANAGED, plan.disposition)
        before = self.fixture.workspace_state()

        result = self._apply(plan.plan_id)

        self.assertIsNone(result.journal)
        self.assertEqual((), result.paths_written)
        self.assertEqual(before, self.fixture.workspace_state())
        self.assertEqual(
            ["--version", "-tf", "-tvf"],
            [arguments[1] for arguments in self.fixture.runner.calls],
        )


if __name__ == "__main__":
    unittest.main()
