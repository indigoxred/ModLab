import io
import json
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapJobState,
    RecoveryResult,
    SetupApplyResult,
)
from modlab.cli import _parser, main
from modlab.adapters.mo2.bootstrap_serialization import BootstrapFormatError
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
from modlab.workflows.skyrim.mo2_bootstrap_rendering import (
    apply_result_to_dict,
    apply_result_to_text,
    plan_result_to_dict,
    plan_result_to_text,
    recovery_result_to_dict,
    recovery_result_to_text,
)
from tests.support.mo2_bootstrap import (
    BootstrapPlanningFixture,
    make_journal_fixture,
    make_receipt_fixture,
    tree_state,
)


PLAN_ID = "bootstrap-plan-sha256:" + "a" * 64
JOB_ID = "bootstrap-job:0123456789abcdef0123456789abcdef"
APPLY_PROGRAMS = (
    r"C:\Windows\System32\tar.exe [version]",
    r"C:\Windows\System32\tar.exe [list-names]",
    r"C:\Windows\System32\tar.exe [list-types]",
    r"C:\Windows\System32\tar.exe [list-names]",
    r"C:\Windows\System32\tar.exe [list-types]",
    r"C:\Windows\System32\tar.exe [extract]",
)


@dataclass(frozen=True)
class CliResult:
    code: int
    stdout: str
    stderr: str


class Mo2BootstrapRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = BootstrapPlanningFixture(Path(self.temporary.name))
        self.preview = self.fixture.prepare()
        self.receipt = make_receipt_fixture(plan_id=self.preview.plan.plan_id)
        self.journal = make_journal_fixture(
            plan_id=self.preview.plan.plan_id,
            state=BootstrapJobState.VERIFIED,
            receipt_id=self.receipt.receipt_id,
        )
        self.created = SetupApplyResult(
            outcome="Created",
            plan_id=self.preview.plan.plan_id,
            journal=self.journal,
            receipt=self.receipt,
            paths_written=tuple(
                sorted(
                    (
                        "runtime/jobs/mo2-bootstrap/"
                        "0123456789abcdef0123456789abcdef/journal.json",
                        "games/skyrim-se-ae/tool-installations/mo2/"
                        f"{self.receipt.receipt_id.removeprefix('bootstrap-receipt-sha256:')}.json",
                    ),
                    key=str.casefold,
                )
            ),
            downloads=(),
            installations=("portable-mo2-create",),
            manager_changes=(
                "tools/mo2/skyrim-se-ae/app/ModOrganizer.exe",
            ),
            game_changes=(),
            programs_launched=APPLY_PROGRAMS,
        )

    def _apply_live_fixture(self):
        self.fixture.runner.calls.clear()
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
                self.preview.plan.plan_id,
                self.fixture.workspace,
                release_path=self.fixture.release_path,
                extractor_path=Path(r"C:\Windows\System32\tar.exe"),
                documents_root=self.fixture.documents_root,
                version_reader=self.fixture.version_reader,
                process_inspector=lambda _: self.fixture.processes,
                command_runner=self.fixture.runner,
                free_space_reader=lambda _: self.fixture.free_bytes,
                clock=lambda: datetime(2026, 8, 31, 3, tzinfo=timezone.utc),
                job_id_factory=lambda: JOB_ID,
            )

    def _recover_live_fixture(self):
        self.fixture.runner.calls.clear()
        with patch.object(
            mo2_bootstrap,
            "load_mo2_release",
            return_value=self.fixture.release,
        ):
            return recover_mo2_setup(
                JOB_ID,
                self.fixture.workspace,
                release_path=self.fixture.release_path,
                extractor_path=Path(r"C:\Windows\System32\tar.exe"),
                documents_root=self.fixture.documents_root,
                version_reader=self.fixture.version_reader,
                process_inspector=lambda _: self.fixture.processes,
                command_runner=self.fixture.runner,
                clock=lambda: datetime(2026, 8, 31, 4, tzinfo=timezone.utc),
            )

    def test_preview_json_has_complete_evidence_and_only_plan_write(self):
        value = plan_result_to_dict(self.preview)

        self.assertEqual(
            {
                "schemaVersion",
                "mo2Setup",
                "pathsWritten",
                "downloadsPerformed",
                "installationActionsPerformed",
                "managerChangesPerformed",
                "gameChangesPerformed",
                "programsLaunched",
            },
            set(value),
        )
        self.assertEqual("Create", value["mo2Setup"]["outcome"])
        self.assertEqual(self.preview.plan.plan_id, value["mo2Setup"]["planId"])
        self.assertEqual(self.preview.plan.plan_id, value["mo2Setup"]["plan"]["planId"])
        self.assertEqual(1, len(value["pathsWritten"]))
        self.assertFalse(Path(value["pathsWritten"][0]).is_absolute())
        self.assertEqual([], value["downloadsPerformed"])
        self.assertEqual([], value["installationActionsPerformed"])
        self.assertEqual([], value["managerChangesPerformed"])
        self.assertEqual([], value["gameChangesPerformed"])
        self.assertEqual(
            [
                r"C:\Windows\System32\tar.exe [version]",
                r"C:\Windows\System32\tar.exe [list-names]",
                r"C:\Windows\System32\tar.exe [list-types]",
            ],
            value["programsLaunched"],
        )

    def test_text_leads_with_outcome_and_ends_with_precise_launch_claim(self):
        preview_text = plan_result_to_text(self.preview)
        created_text = apply_result_to_text(self.created)

        self.assertTrue(preview_text.startswith("Create\n"))
        self.assertIn("No manager changes were performed.", preview_text)
        self.assertTrue(preview_text.endswith("MO2 and Skyrim were not launched.\n"))
        self.assertTrue(created_text.startswith("Created\n"))
        self.assertIn("portable-mo2-create", created_text)
        self.assertIn("tools/mo2/skyrim-se-ae/app/ModOrganizer.exe", created_text)
        self.assertTrue(created_text.endswith("MO2 and Skyrim were not launched.\n"))

    def test_apply_json_exposes_exact_journal_receipt_and_actions(self):
        value = apply_result_to_dict(self.created)

        self.assertEqual("Created", value["mo2Setup"]["outcome"])
        self.assertEqual(self.journal.job_id, value["mo2Setup"]["jobId"])
        self.assertEqual(self.receipt.receipt_id, value["mo2Setup"]["receiptId"])
        self.assertEqual(self.journal.job_id, value["mo2Setup"]["journal"]["jobId"])
        self.assertEqual(
            self.receipt.receipt_id,
            value["mo2Setup"]["receipt"]["receiptId"],
        )
        self.assertEqual(["portable-mo2-create"], value["installationActionsPerformed"])
        self.assertEqual(list(self.created.manager_changes), value["managerChangesPerformed"])

    def test_already_managed_apply_is_an_honest_no_op(self):
        result = replace(
            self.created,
            outcome="AlreadyManaged",
            journal=None,
            paths_written=(),
            installations=(),
            manager_changes=(),
            programs_launched=self.created.programs_launched[:3],
        )

        value = apply_result_to_dict(result)
        text = apply_result_to_text(result)

        self.assertEqual("AlreadyManaged", value["mo2Setup"]["outcome"])
        self.assertIsNone(value["mo2Setup"]["jobId"])
        self.assertTrue(text.startswith("Already managed\n"))
        self.assertIn("No installation actions were performed.", text)

    def test_recovery_json_and_text_report_recovered_without_invented_actions(self):
        recovered_journal = make_journal_fixture(
            plan_id=self.preview.plan.plan_id,
            state=BootstrapJobState.RECOVERED,
            receipt_id=None,
        )
        result = RecoveryResult(
            outcome="Recovered",
            journal=recovered_journal,
            receipt=None,
            paths_written=(
                "runtime/jobs/mo2-bootstrap/"
                "0123456789abcdef0123456789abcdef/journal.json",
            ),
            downloads=(),
            installations=(),
            manager_changes=(),
            game_changes=(),
            programs_launched=(),
        )

        value = recovery_result_to_dict(result)
        text = recovery_result_to_text(result)

        self.assertEqual("Recovered", value["mo2Setup"]["outcome"])
        self.assertIsNone(value["mo2Setup"]["receipt"])
        self.assertEqual([], value["programsLaunched"])
        self.assertTrue(text.startswith("Recovered\n"))
        self.assertIn("No programs were launched.", text)
        self.assertTrue(text.endswith("MO2 and Skyrim were not launched.\n"))

    def test_verified_recovery_text_reports_the_receipt_mode(self):
        result = RecoveryResult(
            outcome="Verified",
            journal=self.journal,
            receipt=self.receipt,
            paths_written=(),
            downloads=(),
            installations=("portable-mo2-create",),
            manager_changes=(),
            game_changes=(),
            programs_launched=(),
        )

        self.assertTrue(recovery_result_to_text(result).startswith("Created\n"))

    def test_real_create_result_satisfies_renderer_contract(self):
        result = self._apply_live_fixture()

        value = apply_result_to_dict(result)

        self.assertEqual("Created", value["mo2Setup"]["outcome"])
        self.assertEqual(
            ["portable-mo2-create"], value["installationActionsPerformed"]
        )
        self.assertTrue(value["managerChangesPerformed"])
        self.assertEqual(list(APPLY_PROGRAMS), value["programsLaunched"])
        self.assertEqual(APPLY_PROGRAMS, result.programs_launched)

    def test_real_adopt_and_already_managed_results_are_manager_no_ops(self):
        self.fixture.make_ready_existing()
        self.preview = self.fixture.prepare()
        adopted = self._apply_live_fixture()
        already_managed = self._apply_live_fixture()

        adopted_value = apply_result_to_dict(adopted)
        managed_value = apply_result_to_dict(already_managed)

        self.assertEqual("Adopted", adopted_value["mo2Setup"]["outcome"])
        self.assertEqual([], adopted_value["managerChangesPerformed"])
        self.assertEqual(list(APPLY_PROGRAMS), adopted_value["programsLaunched"])
        self.assertEqual("AlreadyManaged", managed_value["mo2Setup"]["outcome"])
        self.assertEqual([], managed_value["pathsWritten"])

    def test_real_finalized_recovery_satisfies_renderer_contract(self):
        with patch.object(
            Mo2BootstrapStore,
            "write_receipt",
            side_effect=KeyboardInterrupt("fixture interruption"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._apply_live_fixture()

        result = self._recover_live_fixture()
        value = recovery_result_to_dict(result)
        repeated = self._recover_live_fixture()
        repeated_value = recovery_result_to_dict(repeated)

        self.assertEqual("Verified", value["mo2Setup"]["outcome"])
        self.assertEqual(["portable-mo2-create"], value["installationActionsPerformed"])
        self.assertTrue(recovery_result_to_text(result).startswith("Created\n"))
        self.assertEqual("Verified", repeated_value["mo2Setup"]["outcome"])
        self.assertEqual([], repeated_value["installationActionsPerformed"])
        self.assertEqual([], repeated_value["programsLaunched"])


class Mo2BootstrapCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = BootstrapPlanningFixture(Path(self.temporary.name))
        self.preview = self.fixture.prepare()

    @staticmethod
    def run_cli(arguments) -> CliResult:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(arguments, stdout, stderr)
        return CliResult(code, stdout.getvalue(), stderr.getvalue())

    def setup_arguments(self, *extra):
        return [
            "skyrim",
            "mo2",
            "setup",
            "--artifact",
            self.fixture.artifact_id,
            "--steam-root",
            str(self.fixture.steam_root),
            "--workspace",
            str(self.fixture.workspace),
            *extra,
        ]

    def _live_apply_service(self, plan_id, workspace_root):
        self.fixture.runner.calls.clear()
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
                plan_id,
                workspace_root,
                release_path=self.fixture.release_path,
                extractor_path=Path(r"C:\Windows\System32\tar.exe"),
                documents_root=self.fixture.documents_root,
                version_reader=self.fixture.version_reader,
                process_inspector=lambda _: self.fixture.processes,
                command_runner=self.fixture.runner,
                free_space_reader=lambda _: self.fixture.free_bytes,
                clock=lambda: datetime(2026, 8, 31, 5, tzinfo=timezone.utc),
                job_id_factory=lambda: JOB_ID,
            )

    def _live_recover_service(self, job_id, workspace_root):
        self.fixture.runner.calls.clear()
        with patch.object(
            mo2_bootstrap,
            "load_mo2_release",
            return_value=self.fixture.release,
        ):
            return recover_mo2_setup(
                job_id,
                workspace_root,
                release_path=self.fixture.release_path,
                extractor_path=Path(r"C:\Windows\System32\tar.exe"),
                documents_root=self.fixture.documents_root,
                version_reader=self.fixture.version_reader,
                process_inspector=lambda _: self.fixture.processes,
                command_runner=self.fixture.runner,
                clock=lambda: datetime(2026, 8, 31, 6, tzinfo=timezone.utc),
            )

    def test_parser_has_exact_preview_apply_and_recover_tree(self):
        preview = _parser().parse_args(self.setup_arguments("--format", "json"))
        apply = _parser().parse_args(
            [
                "skyrim", "mo2", "setup", "--apply", PLAN_ID,
                "--workspace", str(self.fixture.workspace),
            ]
        )
        recover = _parser().parse_args(
            [
                "skyrim", "mo2", "recover", JOB_ID,
                "--workspace", str(self.fixture.workspace),
            ]
        )

        self.assertEqual("setup", preview.skyrim_mo2_command)
        self.assertEqual(self.fixture.artifact_id, preview.artifact)
        self.assertIsNone(preview.plan_id)
        self.assertEqual(PLAN_ID, apply.plan_id)
        self.assertIsNone(apply.artifact)
        self.assertEqual("recover", recover.skyrim_mo2_command)
        self.assertEqual(JOB_ID, recover.job_id)

    def test_artifact_and_apply_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            _parser().parse_args(
                [
                    "skyrim", "mo2", "setup",
                    "--artifact", self.fixture.artifact_id,
                    "--apply", PLAN_ID,
                ]
            )

    def test_preview_dispatch_and_steam_fallback(self):
        with patch("modlab.cli.prepare_mo2_setup", return_value=self.preview) as service:
            explicit = self.run_cli(self.setup_arguments("--format", "json"))
            fallback = self.run_cli(
                [
                    "skyrim", "mo2", "setup",
                    "--artifact", self.fixture.artifact_id,
                    "--workspace", str(self.fixture.workspace),
                ]
            )

        self.assertEqual(0, explicit.code)
        self.assertEqual("Create", json.loads(explicit.stdout)["mo2Setup"]["outcome"])
        self.assertEqual("", explicit.stderr)
        self.assertEqual(0, fallback.code)
        self.assertEqual(self.fixture.steam_root, service.call_args_list[0].kwargs["steam_root"])
        self.assertIsNone(service.call_args_list[1].kwargs["steam_root"])

    def test_apply_rejects_steam_root_without_calling_service(self):
        before = tree_state(self.fixture.root)
        with patch("modlab.cli.apply_mo2_setup") as service:
            result = self.run_cli(
                [
                    "skyrim", "mo2", "setup", "--apply", PLAN_ID,
                    "--steam-root", str(self.fixture.steam_root),
                    "--workspace", str(self.fixture.workspace),
                ]
            )

        self.assertEqual(2, result.code)
        self.assertIn("--steam-root", result.stderr)
        service.assert_not_called()
        self.assertEqual(before, tree_state(self.fixture.root))

    def test_apply_and_recover_dispatch_render_service_results(self):
        receipt = make_receipt_fixture(plan_id=self.preview.plan.plan_id)
        journal = make_journal_fixture(
            plan_id=self.preview.plan.plan_id,
            state=BootstrapJobState.VERIFIED,
            receipt_id=receipt.receipt_id,
        )
        applied = SetupApplyResult(
            outcome="Created",
            plan_id=self.preview.plan.plan_id,
            journal=journal,
            receipt=receipt,
            paths_written=tuple(
                sorted(
                    (
                        "runtime/jobs/mo2-bootstrap/"
                        "0123456789abcdef0123456789abcdef/journal.json",
                        "games/skyrim-se-ae/tool-installations/mo2/"
                        f"{receipt.receipt_id.removeprefix('bootstrap-receipt-sha256:')}.json",
                    ),
                    key=str.casefold,
                )
            ),
            downloads=(),
            installations=("portable-mo2-create",),
            manager_changes=(
                "tools/mo2/skyrim-se-ae/app/ModOrganizer.exe",
            ),
            game_changes=(),
            programs_launched=APPLY_PROGRAMS,
        )
        recovered = RecoveryResult(
            outcome="Verified",
            journal=journal,
            receipt=receipt,
            paths_written=(),
            downloads=(),
            installations=("portable-mo2-create",),
            manager_changes=(),
            game_changes=(),
            programs_launched=(),
        )
        with patch("modlab.cli.apply_mo2_setup", return_value=applied) as apply_service:
            apply_result = self.run_cli(
                [
                    "skyrim", "mo2", "setup", "--apply", self.preview.plan.plan_id,
                    "--workspace", str(self.fixture.workspace), "--format", "json",
                ]
            )
        with patch("modlab.cli.recover_mo2_setup", return_value=recovered) as recover_service:
            recover_result = self.run_cli(
                [
                    "skyrim", "mo2", "recover", journal.job_id,
                    "--workspace", str(self.fixture.workspace), "--format", "json",
                ]
            )

        self.assertEqual(0, apply_result.code)
        self.assertEqual("Created", json.loads(apply_result.stdout)["mo2Setup"]["outcome"])
        self.assertEqual(0, recover_result.code)
        self.assertEqual("Verified", json.loads(recover_result.stdout)["mo2Setup"]["outcome"])
        apply_service.assert_called_once_with(self.preview.plan.plan_id, self.fixture.workspace)
        recover_service.assert_called_once_with(journal.job_id, self.fixture.workspace)

    def test_blocked_preview_and_safe_refusals_use_exit_three(self):
        self.fixture.make_unknown_target()
        blocked = self.fixture.prepare()
        with patch("modlab.cli.prepare_mo2_setup", return_value=blocked):
            blocked_result = self.run_cli(self.setup_arguments("--format", "json"))
        with patch(
            "modlab.cli.apply_mo2_setup",
            side_effect=Mo2BootstrapRefusal(
                "recovery-required", "exact interrupted job must be recovered"
            ),
        ):
            refused = self.run_cli(
                [
                    "skyrim", "mo2", "setup", "--apply", self.preview.plan.plan_id,
                    "--workspace", str(self.fixture.workspace), "--format", "json",
                ]
            )

        self.assertEqual(3, blocked_result.code)
        self.assertEqual("Blocked", json.loads(blocked_result.stdout)["mo2Setup"]["outcome"])
        self.assertEqual(3, refused.code)
        refusal = json.loads(refused.stdout)
        self.assertEqual("RecoveryRequired", refusal["mo2Setup"]["outcome"])
        self.assertFalse(refusal["mo2Setup"]["actionEvidenceComplete"])
        for key in (
            "pathsWritten",
            "downloadsPerformed",
            "installationActionsPerformed",
            "managerChangesPerformed",
            "gameChangesPerformed",
            "programsLaunched",
        ):
            self.assertEqual([], refusal[key])
        self.assertNotIn("Traceback", refused.stderr)

    def test_real_changed_precondition_reports_completed_archive_actions(self):
        (self.fixture.layout.skyrim_mo2 / "appeared.txt").write_bytes(b"changed")
        with patch(
            "modlab.cli.apply_mo2_setup",
            side_effect=self._live_apply_service,
        ):
            result = self.run_cli(
                [
                    "skyrim", "mo2", "setup",
                    "--apply", self.preview.plan.plan_id,
                    "--workspace", str(self.fixture.workspace),
                    "--format", "json",
                ]
            )

        value = json.loads(result.stdout)
        self.assertEqual(3, result.code)
        self.assertEqual("Blocked", value["mo2Setup"]["outcome"])
        self.assertTrue(value["mo2Setup"]["actionEvidenceComplete"])
        self.assertIsNone(value["mo2Setup"]["jobId"])
        self.assertEqual([], value["pathsWritten"])
        self.assertEqual(
            [
                r"C:\Windows\System32\tar.exe [version]",
                r"C:\Windows\System32\tar.exe [list-names]",
                r"C:\Windows\System32\tar.exe [list-types]",
            ],
            value["programsLaunched"],
        )

    def test_real_post_activation_refusal_reports_recovery_job_and_actions(self):
        with patch(
            "modlab.cli.apply_mo2_setup",
            side_effect=self._live_apply_service,
        ), patch.object(
            mo2_bootstrap,
            "_observe_existing_manager",
            return_value=None,
        ):
            result = self.run_cli(
                [
                    "skyrim", "mo2", "setup",
                    "--apply", self.preview.plan.plan_id,
                    "--workspace", str(self.fixture.workspace),
                    "--format", "json",
                ]
            )

        value = json.loads(result.stdout)
        setup = value["mo2Setup"]
        self.assertEqual(3, result.code)
        self.assertEqual("RecoveryRequired", setup["outcome"])
        self.assertEqual("post-activation-not-ready", setup["code"])
        self.assertTrue(setup["actionEvidenceComplete"])
        self.assertEqual(JOB_ID, setup["jobId"])
        self.assertEqual("RecoveryRequired", setup["journal"]["state"])
        self.assertTrue(value["pathsWritten"])
        self.assertTrue(value["managerChangesPerformed"])
        self.assertEqual(
            r"C:\Windows\System32\tar.exe [extract]",
            value["programsLaunched"][-1],
        )
        self.assertEqual(list(APPLY_PROGRAMS), value["programsLaunched"])

    def test_real_post_quarantine_failure_reports_both_recovery_paths(self):
        transition = Mo2BootstrapStore.transition_job

        def interrupt_before_applying(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.APPLYING:
                raise KeyboardInterrupt("fixture interruption before Applying")
            return transition(store, observed, expected, replacement)

        with patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=interrupt_before_applying,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._live_apply_service(self.preview.plan.plan_id, self.fixture.workspace)

        def fail_recovery_journal(store, observed, expected, replacement):
            if replacement.state is BootstrapJobState.RECOVERY_REQUIRED:
                raise OSError("fixture recovery journal failure")
            return transition(store, observed, expected, replacement)

        with patch(
            "modlab.cli.recover_mo2_setup",
            side_effect=self._live_recover_service,
        ), patch.object(
            Mo2BootstrapStore,
            "transition_job",
            new=fail_recovery_journal,
        ):
            result = self.run_cli(
                [
                    "skyrim", "mo2", "recover", JOB_ID,
                    "--workspace", str(self.fixture.workspace),
                    "--format", "json",
                ]
            )

        value = json.loads(result.stdout)
        setup = value["mo2Setup"]
        self.assertEqual(3, result.code)
        self.assertEqual("RecoveryRequired", setup["outcome"])
        self.assertTrue(setup["actionEvidenceComplete"])
        self.assertEqual(JOB_ID, setup["jobId"])
        changes = value["managerChangesPerformed"]
        from modlab.adapters.mo2.path_budget import stage_name
        expected_stage = f"tools/mo2/{stage_name(JOB_ID, 2)}"
        self.assertIn(expected_stage, changes)
        self.assertTrue(any(path.endswith("/recovered-stage") for path in changes))
        self.assertEqual([], value["programsLaunched"])

    def test_create_and_adopt_report_a_journal_promoted_before_reload_failure(self):
        original = Mo2BootstrapStore.load_job

        for disposition in ("Create", "Adopt"):
            with self.subTest(disposition=disposition):
                isolated = tempfile.TemporaryDirectory()
                self.addCleanup(isolated.cleanup)
                self.fixture = BootstrapPlanningFixture(Path(isolated.name))
                self.preview = self.fixture.prepare()
                if disposition == "Adopt":
                    self.fixture.make_ready_existing()
                    self.preview = self.fixture.prepare()
                failed = False

                def fail_first_promoted_reload(store, job_id, *, changed=False):
                    nonlocal failed
                    if changed and not failed:
                        failed = True
                        raise Mo2BootstrapStoreError(
                            "fixture journal reload failed after promotion"
                        )
                    return original(store, job_id, changed=changed)

                with patch(
                    "modlab.cli.apply_mo2_setup",
                    side_effect=self._live_apply_service,
                ), patch.object(
                    Mo2BootstrapStore,
                    "load_job",
                    new=fail_first_promoted_reload,
                ):
                    result = self.run_cli(
                        [
                            "skyrim", "mo2", "setup",
                            "--apply", self.preview.plan.plan_id,
                            "--workspace", str(self.fixture.workspace),
                            "--format", "json",
                        ]
                    )

                value = json.loads(result.stdout)
                setup = value["mo2Setup"]
                self.assertEqual(3, result.code)
                self.assertEqual("RecoveryRequired", setup["outcome"])
                self.assertTrue(setup["actionEvidenceComplete"])
                self.assertEqual(JOB_ID, setup["jobId"])
                self.assertIsNone(setup["journal"])
                self.assertEqual(
                    [
                        "runtime/jobs/mo2-bootstrap/"
                        "0123456789abcdef0123456789abcdef/journal.json"
                    ],
                    value["pathsWritten"],
                )

    def test_create_and_adopt_report_a_receipt_promoted_before_reload_failure(self):
        original = Mo2BootstrapStore.load_receipt

        for disposition in ("Create", "Adopt"):
            with self.subTest(disposition=disposition):
                isolated = tempfile.TemporaryDirectory()
                self.addCleanup(isolated.cleanup)
                self.fixture = BootstrapPlanningFixture(Path(isolated.name))
                self.preview = self.fixture.prepare()
                if disposition == "Adopt":
                    self.fixture.make_ready_existing()
                    self.preview = self.fixture.prepare()
                failed = False

                def fail_first_promoted_reload(store, receipt_id):
                    nonlocal failed
                    if not failed:
                        failed = True
                        raise Mo2BootstrapStoreError(
                            "fixture receipt reload failed after promotion"
                        )
                    return original(store, receipt_id)

                with patch(
                    "modlab.cli.apply_mo2_setup",
                    side_effect=self._live_apply_service,
                ), patch.object(
                    Mo2BootstrapStore,
                    "load_receipt",
                    new=fail_first_promoted_reload,
                ):
                    result = self.run_cli(
                        [
                            "skyrim", "mo2", "setup",
                            "--apply", self.preview.plan.plan_id,
                            "--workspace", str(self.fixture.workspace),
                            "--format", "json",
                        ]
                    )

                value = json.loads(result.stdout)
                setup = value["mo2Setup"]
                self.assertEqual(3, result.code)
                self.assertEqual("RecoveryRequired", setup["outcome"])
                self.assertTrue(setup["actionEvidenceComplete"])
                self.assertEqual(JOB_ID, setup["jobId"])
                self.assertIsNotNone(setup["journal"])
                self.assertIsNone(setup["receipt"])
                self.assertTrue(
                    any(
                        path.startswith(
                            "games/skyrim-se-ae/tool-installations/mo2/"
                        )
                        and path.endswith(".json")
                        for path in value["pathsWritten"]
                    )
                )
                self.assertEqual(list(APPLY_PROGRAMS), value["programsLaunched"])

    def test_invalid_typed_identifiers_exit_two_without_service_calls(self):
        with patch("modlab.cli.prepare_mo2_setup") as preview_service:
            bad_artifact = self.run_cli(
                [
                    "skyrim", "mo2", "setup", "--artifact", "bad",
                    "--workspace", str(self.fixture.workspace),
                ]
            )
        with patch("modlab.cli.apply_mo2_setup") as apply_service:
            bad_plan = self.run_cli(
                [
                    "skyrim", "mo2", "setup", "--apply", "bad",
                    "--workspace", str(self.fixture.workspace),
                ]
            )
        with patch("modlab.cli.recover_mo2_setup") as recover_service:
            bad_job = self.run_cli(
                [
                    "skyrim", "mo2", "recover", "bad",
                    "--workspace", str(self.fixture.workspace),
                ]
            )

        self.assertEqual((2, 2, 2), (bad_artifact.code, bad_plan.code, bad_job.code))
        preview_service.assert_not_called()
        apply_service.assert_not_called()
        recover_service.assert_not_called()

    def test_expected_apply_refusals_are_clean_action_free_results(self):
        for code, message in (
            ("plan-invalid", "stored plan was modified"),
            ("process-running", "ModOrganizer.exe is still running"),
            ("target-changed", "the manager target changed"),
            ("profile-seed-changed", "the INI seed changed"),
            ("post-activation-not-ready", "the manager did not project Ready"),
        ):
            with self.subTest(code=code), patch(
                "modlab.cli.apply_mo2_setup",
                side_effect=Mo2BootstrapRefusal(code, message),
            ):
                result = self.run_cli(
                    [
                        "skyrim", "mo2", "setup",
                        "--apply", self.preview.plan.plan_id,
                        "--workspace", str(self.fixture.workspace),
                        "--format", "json",
                    ]
                )

            value = json.loads(result.stdout)
            self.assertEqual(3, result.code)
            self.assertEqual("Blocked", value["mo2Setup"]["outcome"])
            self.assertEqual(code, value["mo2Setup"]["code"])
            self.assertFalse(value["mo2Setup"]["actionEvidenceComplete"])
            self.assertEqual("", result.stderr)
            for key in (
                "pathsWritten",
                "downloadsPerformed",
                "installationActionsPerformed",
                "managerChangesPerformed",
                "gameChangesPerformed",
                "programsLaunched",
            ):
                self.assertEqual([], value[key])

    def test_invalid_schema_and_unsupported_release_exit_two(self):
        with patch(
            "modlab.cli.apply_mo2_setup",
            side_effect=BootstrapFormatError("schemaVersion must be integer 1"),
        ):
            invalid = self.run_cli(
                [
                    "skyrim", "mo2", "setup",
                    "--apply", self.preview.plan.plan_id,
                    "--workspace", str(self.fixture.workspace),
                ]
            )
        with patch(
            "modlab.cli.prepare_mo2_setup",
            side_effect=Mo2BootstrapRefusal(
                "release-unsupported", "requested release is not curated"
            ),
        ):
            unsupported = self.run_cli(self.setup_arguments())

        self.assertEqual(2, invalid.code)
        self.assertIn("schemaVersion", invalid.stderr)
        self.assertNotIn("Traceback", invalid.stderr)
        self.assertEqual(2, unsupported.code)
        self.assertTrue(unsupported.stdout.startswith("Blocked\n"))
        self.assertIn("Action evidence is incomplete", unsupported.stdout)
        self.assertNotIn("No programs were launched", unsupported.stdout)
        self.assertNotIn("Traceback", unsupported.stderr)

    def test_unexpected_service_exception_remains_visible(self):
        for error in (
            RuntimeError("developer-visible runtime failure"),
            ValueError("developer-visible value failure"),
        ):
            with self.subTest(error=type(error).__name__), patch(
                "modlab.cli.prepare_mo2_setup",
                side_effect=error,
            ):
                with self.assertRaisesRegex(type(error), "developer-visible"):
                    self.run_cli(self.setup_arguments())


if __name__ == "__main__":
    unittest.main()
