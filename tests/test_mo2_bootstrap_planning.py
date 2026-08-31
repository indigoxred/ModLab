import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_config import classify_mo2_target
from modlab.adapters.mo2.bootstrap_model import (
    BootstrapDisposition,
    ProcessIdentity,
    ProcessObservation,
)
from modlab.workflows.skyrim.drift import SkyrimDriftReport, SkyrimStatusOutcome
from tests.support.mo2_bootstrap import BootstrapPlanningFixture


class Mo2BootstrapPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = BootstrapPlanningFixture(Path(self.temporary.name))

    def test_empty_target_plans_create_and_writes_only_plan(self):
        before_external = self.fixture.external_state()

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.CREATE, result.plan.disposition)
        self.assertEqual((result.plan_path,), result.paths_written)
        self.assertEqual(before_external, self.fixture.external_state())
        self.assertEqual((), result.downloads)
        self.assertEqual((), result.installations)
        self.assertEqual((), result.manager_changes)
        self.assertEqual((), result.game_changes)
        self.assertEqual(
            (
                r"C:\Windows\System32\tar.exe [version]",
                r"C:\Windows\System32\tar.exe [list-names]",
                r"C:\Windows\System32\tar.exe [list-types]",
            ),
            result.programs_launched,
        )
        self.assertEqual(
            ["--version", "-tf", "-tvf"],
            [arguments[1] for arguments in self.fixture.runner.calls],
        )

    def test_ready_existing_target_plans_adopt_even_when_process_close_is_required(self):
        self.fixture.make_ready_existing()
        self.fixture.processes = ProcessObservation(
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

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.ADOPT, result.plan.disposition)
        self.assertTrue(result.plan.processes.relevant)
        running = next(
            item for item in result.plan.findings if item.code == "mo2-process-running"
        )
        self.assertEqual("Warning", running.state.value)

    def test_same_inputs_return_same_plan_id_and_bytes(self):
        first = self.fixture.prepare()
        first_bytes = first.plan_path.read_bytes()

        second = self.fixture.prepare()

        self.assertEqual(first.plan.plan_id, second.plan.plan_id)
        self.assertEqual(first_bytes, second.plan_path.read_bytes())
        self.assertEqual((), second.paths_written)

    def test_verified_matching_receipt_is_already_managed_but_profile_drift_is_adopt(self):
        self.fixture.make_receipt_covered()

        managed = self.fixture.prepare()

        self.assertEqual(
            BootstrapDisposition.ALREADY_MANAGED, managed.plan.disposition
        )
        profile_ini = (
            self.fixture.layout.skyrim_mo2_profiles
            / "ModLab - Lab"
            / "Skyrim.ini"
        )
        profile_ini.write_bytes(b"[General]\nchanged=true\n")

        stale = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.ADOPT, stale.plan.disposition)

    def test_verified_receipt_with_low_space_remains_already_managed(self):
        self.fixture.make_receipt_covered()
        self.fixture.free_bytes = 1

        def staging_space_must_not_be_read(_):
            raise AssertionError("verified no-op must not require staging space")

        result = self.fixture.prepare(
            free_space_reader=staging_space_must_not_be_read
        )

        self.assertEqual(
            BootstrapDisposition.ALREADY_MANAGED, result.plan.disposition
        )
        finding = next(
            item
            for item in result.plan.findings
            if item.code == "disk-space-insufficient"
        )
        self.assertEqual("Passed", finding.state.value)

    def test_immutable_package_drift_prevents_already_managed(self):
        self.fixture.make_receipt_covered()
        packaged = self.fixture.layout.skyrim_mo2_app / "loot" / "loot.dll"
        packaged.write_bytes(b"changed immutable package file")

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.ADOPT, result.plan.disposition)

    def test_new_unknown_manager_extra_prevents_already_managed(self):
        self.fixture.make_receipt_covered()
        (self.fixture.layout.skyrim_mo2_app / "unknown.dll").write_bytes(b"unknown")

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.ADOPT, result.plan.disposition)

    def test_missing_explicit_steam_root_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "Steam root"):
            self.fixture.prepare(steam_root=None)

        self.assertEqual(before, self.fixture.workspace_state())

    def test_modified_archive_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        metadata_hash = self.fixture.artifact_id.removeprefix("archive-sha256:")
        archive = (
            self.fixture.workspace
            / "library"
            / "archives"
            / metadata_hash[:2]
            / metadata_hash
            / "payload.7z"
        )
        archive.write_bytes(b"modified")
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "archive"):
            self.fixture.prepare()

        self.assertEqual(before, self.fixture.workspace_state())

    def test_missing_archive_payload_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        archive_hash = self.fixture.artifact_id.removeprefix("archive-sha256:")
        payload = (
            self.fixture.workspace
            / "library"
            / "archives"
            / archive_hash[:2]
            / archive_hash
            / "payload.7z"
        )
        payload.unlink()
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare()

        self.assertEqual("artifact-missing", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_release_and_artifact_descriptor_mismatch_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        self.fixture.release = replace(
            self.fixture.release,
            descriptor=replace(
                self.fixture.release.descriptor,
                archive_name="different-package.7z",
            ),
        )
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "curated MO2 release"):
            self.fixture.prepare()

        self.assertEqual(before, self.fixture.workspace_state())

    def test_unsupported_release_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        self.fixture.release = replace(
            self.fixture.release,
            descriptor=replace(
                self.fixture.release.descriptor,
                release_id="mo2.windows-portable.future",
            ),
        )
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare()

        self.assertEqual("release-unsupported", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_archive_listing_drift_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        names = [*self.fixture.archive_names[:-1], "different.dll"]
        name_bytes = ("\n".join(names) + "\n").encode("utf-8")
        verbose = "\n".join(
            f"-rw-r--r--  0 0 0 0 Jan 01 2026 {name}" for name in names
        )
        self.fixture.runner.responses["-tf"] = CompletedProcess(
            (), 0, name_bytes, b""
        )
        self.fixture.runner.responses["-tvf"] = CompletedProcess(
            (), 0, (verbose + "\n").encode("utf-8"), b""
        )
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare()

        self.assertEqual("archive-listing-mismatch", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_unsafe_archive_listing_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        self.fixture.runner.responses["-tf"] = CompletedProcess(
            (), 0, b"../outside.dll\n", b""
        )
        self.fixture.runner.responses["-tvf"] = CompletedProcess(
            (), 0, b"-rw-r--r--  0 0 0 0 Jan 01 2026 ../outside.dll\n", b""
        )
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare()

        self.assertEqual("archive-listing-unsafe", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_missing_extractor_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare(extractor_path=self.fixture.root / "missing-tar.exe")

        self.assertEqual("extractor-missing", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_failed_extractor_identity_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        self.fixture.runner.responses["--version"] = CompletedProcess(
            (), 5, b"", b"fixture failure"
        )
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare()

        self.assertEqual("extractor-failed", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_extractor_launch_oserror_is_a_stable_refusal(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        class BrokenRunner:
            def run(self, _):
                raise OSError("fixture launch failure")

        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare(command_runner=BrokenRunner())

        self.assertEqual("extractor-failed", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_low_disk_space_retains_a_coherent_blocked_plan(self):
        self.fixture.free_bytes = 1

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        finding = next(
            item
            for item in result.plan.findings
            if item.code == "disk-space-insufficient"
        )
        self.assertEqual("Blocked", finding.state.value)

    def test_unknown_nonempty_target_retains_blocked_plan(self):
        self.fixture.make_unknown_target()

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        self.assertIn(
            "target-unknown-nonempty",
            {item.code for item in result.plan.findings},
        )

    def test_unknown_process_observation_retains_blocked_plan(self):
        self.fixture.processes = ProcessObservation(
            complete=False,
            relevant=(),
            error="access denied",
        )

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        finding = next(
            item
            for item in result.plan.findings
            if item.code == "process-inspection-unknown"
        )
        self.assertEqual("Blocked", finding.state.value)

    def test_conflicting_configured_steam_root_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        self.fixture.make_environment()
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "conflicts"):
            self.fixture.prepare(steam_root=self.fixture.root / "Other Steam")

        self.assertEqual(before, self.fixture.workspace_state())

    def test_drifted_selected_baseline_retains_blocked_plan(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        self.fixture.make_ready_existing()
        baseline_id = "checkpoint-sha256:" + "c" * 64
        self.fixture.make_environment(baseline_id=baseline_id)
        drifted = SkyrimDriftReport(
            outcome=SkyrimStatusOutcome.DRIFTED,
            baseline_checkpoint_id=baseline_id,
            domains=(),
            findings=("fixture drift",),
        )

        with patch.object(mo2_bootstrap, "get_skyrim_status", return_value=drifted):
            result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        self.assertEqual("Drifted", result.plan.baseline_status)

    def test_blocked_selected_baseline_retains_blocked_plan(self):
        from modlab.workflows.skyrim import mo2_bootstrap

        self.fixture.make_ready_existing()
        baseline_id = "checkpoint-sha256:" + "c" * 64
        self.fixture.make_environment(baseline_id=baseline_id)
        blocked = SkyrimDriftReport(
            outcome=SkyrimStatusOutcome.BLOCKED,
            baseline_checkpoint_id=baseline_id,
            domains=(),
            findings=("fixture blocked",),
        )

        with patch.object(mo2_bootstrap, "get_skyrim_status", return_value=blocked):
            result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        self.assertEqual("Blocked", result.plan.baseline_status)

    def test_existing_projection_and_wrong_executable_each_block(self):
        self.fixture.make_ready_existing()
        (self.fixture.layout.skyrim_mo2_profiles / "ModLab - Play" / "settings.ini").unlink()

        projection_blocked = self.fixture.prepare()

        self.assertEqual(
            BootstrapDisposition.BLOCKED, projection_blocked.plan.disposition
        )
        self.assertIn(
            "post-activation-not-ready",
            {item.code for item in projection_blocked.plan.findings},
        )

        (self.fixture.layout.skyrim_mo2_profiles / "ModLab - Play" / "settings.ini").write_bytes(
            b"[General]\nLocalSaves=false\nLocalSettings=true\nAutomaticArchiveInvalidation=true\n"
        )
        (self.fixture.layout.skyrim_mo2_app / "ModOrganizer.exe").write_bytes(
            b"wrong executable"
        )

        package_blocked = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, package_blocked.plan.disposition)
        finding = next(
            item
            for item in package_blocked.plan.findings
            if item.code == "existing-package-mismatch"
        )
        self.assertEqual("Blocked", finding.state.value)

    def test_invalid_skyrim_ccc_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        (self.fixture.game_root / "Skyrim.ccc").write_bytes(b"../outside.esp\n")
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "Skyrim.ccc"):
            self.fixture.prepare()

        self.assertEqual(before, self.fixture.workspace_state())

    def test_redirected_ini_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        ini = (
            self.fixture.documents_root
            / "My Games"
            / "Skyrim Special Edition"
            / "SkyrimCustom.ini"
        )
        outside = self.fixture.root / "outside.ini"
        outside.write_bytes(b"outside")
        ini.unlink()
        try:
            os.symlink(outside, ini)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare()

        self.assertEqual("profile-seed-changed", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_malformed_receipt_retains_blocked_plan(self):
        receipt = self.fixture.layout.mo2_bootstrap_receipts / ("d" * 64 + ".json")
        receipt.write_bytes(b'{"invalid":true}\n')

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        self.assertIn("receipt-invalid", {item.code for item in result.plan.findings})

    def test_invalid_journal_and_orphan_stage_each_retain_blocked_plan(self):
        job_hex = "1" * 32
        job = self.fixture.layout.mo2_bootstrap_jobs / job_hex
        job.mkdir()
        (job / "journal.json").write_bytes(b'{"invalid":true}\n')

        journal_result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, journal_result.plan.disposition)
        self.assertIn(
            "journal-invalid", {item.code for item in journal_result.plan.findings}
        )

        shutil.rmtree(job)
        stage = (
            self.fixture.layout.skyrim_mo2.parent
            / (".skyrim-se-ae.modlab-stage-" + "2" * 32)
        )
        stage.mkdir()

        stage_result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, stage_result.plan.disposition)
        self.assertIn("orphan-stage", {item.code for item in stage_result.plan.findings})

    def test_active_job_stage_collision_requires_recovery(self):
        from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore

        self.fixture.make_ready_existing()
        plan = self.fixture.prepare().plan
        store = Mo2BootstrapStore(self.fixture.workspace)
        job = store.create_job(plan)
        Path(job.journal.stage_root).mkdir()

        result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        codes = {item.code for item in result.plan.findings}
        self.assertIn("stage-collision", codes)
        self.assertIn("recovery-required", codes)

    def test_redirected_workspace_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        link = self.fixture.root / "workspace-link"
        try:
            os.symlink(self.fixture.workspace, link, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare(workspace_root=link)

        self.assertEqual("plan-invalid", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_nested_archive_redirect_is_refused_before_reading_payload(self):
        from modlab.workflows.skyrim import mo2_bootstrap
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        digest = self.fixture.artifact_id.removeprefix("archive-sha256:")
        redirected = self.fixture.layout.archives / digest[:2]
        original = mo2_bootstrap._redirected

        def mark_nested(path, metadata):
            return Path(path) == redirected or original(path, metadata)

        before = self.fixture.workspace_state()
        with patch.object(mo2_bootstrap, "_redirected", side_effect=mark_nested):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self.fixture.prepare()

        self.assertEqual("artifact-modified", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_unsafe_existing_package_path_blocks_instead_of_downgrading_to_adopt(self):
        from modlab.adapters.mo2.archive import Mo2ArchiveError
        from modlab.workflows.skyrim import mo2_bootstrap

        self.fixture.make_ready_existing()
        with patch.object(
            mo2_bootstrap,
            "inventory_tree",
            side_effect=Mo2ArchiveError("package entry is redirected"),
        ):
            result = self.fixture.prepare()

        self.assertEqual(BootstrapDisposition.BLOCKED, result.plan.disposition)
        finding = next(
            item
            for item in result.plan.findings
            if item.code == "existing-package-mismatch"
        )
        self.assertEqual("Blocked", finding.state.value)

    def test_manager_change_during_stability_reread_refuses_without_plan(self):
        from modlab.workflows.skyrim import mo2_bootstrap
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        self.fixture.make_ready_existing()
        original = mo2_bootstrap._observe_existing_manager
        calls = 0

        def mutate_before_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                packaged = self.fixture.layout.skyrim_mo2_app / "loot" / "loot.dll"
                packaged.write_bytes(b"changed during stability pass")
            return original(*args, **kwargs)

        plans_before = tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir())
        with patch.object(
            mo2_bootstrap,
            "_observe_existing_manager",
            side_effect=mutate_before_second,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self.fixture.prepare()

        self.assertEqual("target-changed", raised.exception.code)
        self.assertEqual(plans_before, tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir()))

    def test_seed_change_during_stability_reread_refuses_without_plan(self):
        from modlab.workflows.skyrim import mo2_bootstrap
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        original = mo2_bootstrap.observe_profile_seed
        calls = 0

        def mutate_before_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                ini = (
                    self.fixture.documents_root
                    / "My Games"
                    / "Skyrim Special Edition"
                    / "Skyrim.ini"
                )
                ini.write_bytes(b"[General]\nchanged=true\n")
            return original(*args, **kwargs)

        with patch.object(
            mo2_bootstrap,
            "observe_profile_seed",
            side_effect=mutate_before_second,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self.fixture.prepare()

        self.assertEqual("profile-seed-changed", raised.exception.code)
        self.assertEqual((), tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir()))

    def test_extractor_change_during_stability_reread_refuses_without_plan(self):
        from modlab.workflows.skyrim import mo2_bootstrap
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        original = mo2_bootstrap._stable_direct_file_hash

        def changed_tar(path, label):
            observed = original(path, label)
            if Path(path).name.casefold() == "tar.exe":
                return ("f" * 64, observed[1])
            return observed

        with patch.object(
            mo2_bootstrap,
            "_stable_direct_file_hash",
            side_effect=changed_tar,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self.fixture.prepare()

        self.assertEqual("extractor-changed", raised.exception.code)
        self.assertEqual((), tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir()))

    def test_receipt_change_during_stability_reread_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal
        from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore

        self.fixture.make_receipt_covered()
        original = Mo2BootstrapStore.find_compatible_receipt
        calls = 0

        def disappear_on_second(store, match):
            nonlocal calls
            calls += 1
            observed = original(store, match)
            return None if calls == 2 else observed

        plans_before = tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir())
        with patch.object(
            Mo2BootstrapStore,
            "find_compatible_receipt",
            new=disappear_on_second,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self.fixture.prepare()

        self.assertEqual("receipt-invalid", raised.exception.code)
        self.assertEqual(plans_before, tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir()))

    def test_new_malformed_receipt_on_stability_reread_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal
        from modlab.workflows.skyrim.mo2_bootstrap_store import Mo2BootstrapStore

        original = Mo2BootstrapStore.find_compatible_receipt
        calls = 0

        def appear_on_second(store, match):
            nonlocal calls
            calls += 1
            if calls == 2:
                malformed = store.layout.mo2_bootstrap_receipts / ("e" * 64 + ".json")
                malformed.write_bytes(b'{"invalid":true}\n')
            return original(store, match)

        with patch.object(
            Mo2BootstrapStore,
            "find_compatible_receipt",
            new=appear_on_second,
        ):
            with self.assertRaises(Mo2BootstrapRefusal) as raised:
                self.fixture.prepare()

        self.assertEqual("receipt-invalid", raised.exception.code)
        self.assertEqual((), tuple(self.fixture.layout.mo2_bootstrap_plans.iterdir()))

    def test_missing_primary_master_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        (self.fixture.game_root / "Data" / "Update.esm").unlink()
        before = self.fixture.workspace_state()

        with self.assertRaisesRegex(Mo2BootstrapRefusal, "Update.esm"):
            self.fixture.prepare()

        self.assertEqual(before, self.fixture.workspace_state())

    def test_unknown_skyrim_runtime_refuses_without_plan(self):
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        before = self.fixture.workspace_state()

        with self.assertRaises(Mo2BootstrapRefusal) as raised:
            self.fixture.prepare(version_reader=lambda _: None)

        self.assertEqual("skyrim-discovery-blocked", raised.exception.code)
        self.assertEqual(before, self.fixture.workspace_state())

    def test_target_change_during_stability_reread_refuses_without_plan(self):
        from modlab.workflows.skyrim import mo2_bootstrap
        from modlab.workflows.skyrim.mo2_bootstrap import Mo2BootstrapRefusal

        original = classify_mo2_target(self.fixture.layout)
        changed = replace(original, inventory_sha256="f" * 64)
        before = self.fixture.workspace_state()

        with patch.object(
            mo2_bootstrap,
            "classify_mo2_target",
            side_effect=(original, changed),
        ):
            with self.assertRaisesRegex(Mo2BootstrapRefusal, "target changed"):
                self.fixture.prepare()

        self.assertEqual(before, self.fixture.workspace_state())


if __name__ == "__main__":
    unittest.main()
