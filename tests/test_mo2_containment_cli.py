import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modlab.validation.mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    IntegrityObservation,
    ProcessEvidence,
    ProtectedState,
    ScenarioCleanupStatus,
    ScenarioJournal,
    ScenarioOutcome,
    ScenarioRecovery,
    ScenarioResult,
    ScenarioState,
    TreeIdentity,
    WatchEvidenceCompletion,
    WatcherEvent,
)
from modlab.validation import mo2_containment_cli as cli
from modlab.validation import mo2_containment_service as containment_service
from modlab.validation.mo2_containment_store import (
    ContainmentStoreError,
    ContainmentStoreMalformedEvidence,
    ContainmentStoreNotFound,
)


RUN_ID = "containment-run:" + "a" * 32
ARTIFACT_ID = "archive-sha256:" + "b" * 64
EFFECT_ROOT = Path(r"C:\service-effects")


class PreparationCliTests(unittest.TestCase):
    def test_preparation_commands_reach_live_readonly_absent_root_refusal(self):
        for command in ("recover-preparation", "restart-preparation"):
            with self.subTest(command=command), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
                absent = Path(directory) / "absent"
                output = io.StringIO()
                code = cli.main([command, RUN_ID, "--workspace", str(absent), "--format", "json"], stdout=output)
                self.assertEqual(3, code, "valid preparation command must reach the service refusal, not parser rejection")
                value = json.loads(output.getvalue())
                self.assertEqual(command, value["command"])
                self.assertEqual([], value["writtenPaths"])
                self.assertEqual([], value["launchedProcesses"])
                self.assertIsNone(value["verdict"])
                self.assertFalse(absent.exists())


def tree() -> TreeIdentity:
    return TreeIdentity("c" * 64, 1, 1, 1)


def protected() -> ProtectedState:
    value = tree()
    return ProtectedState(value, "d" * 64, "d" * 64, value, value, value)


def result(outcome: ScenarioOutcome = ScenarioOutcome.PASSED) -> ScenarioResult:
    state = protected()
    process = ProcessEvidence(
        2468,
        r"C:\validation\ModOrganizer.exe",
        "2.5.2.0",
        ("--profile", "ModLab - Lab"),
        r"C:\validation",
        IntegrityObservation.LOW,
    )
    return ScenarioResult(
        1,
        RUN_ID,
        ContainmentScenario.MERGE_EXISTING,
        outcome,
        state,
        state,
        process,
        IntegrityObservation.MEDIUM,
        IntegrityObservation.LOW,
        "watch-outcome-sha256:" + "e" * 64,
        WatchEvidenceCompletion.COMPLETED,
        True,
        False,
        (),
        1,
        True,
        True,
        0,
        (),
        True,
        (),
        True,
        (),
        True,
        None,
        None,
        None,
        True,
        () if outcome is ScenarioOutcome.PASSED else ("proven-containment-breach",),
    )


class FakeService:
    def __init__(
        self,
        *,
        scenario_result: ScenarioResult | None = None,
        decision: CapabilityDecision | None = None,
        recovery: ScenarioRecovery | None = None,
        effects: dict[str, object] | None = None,
    ) -> None:
        self.scenario_result = scenario_result or result()
        self.decision = decision
        self.recovery = recovery or ScenarioRecovery(
            1,
            RUN_ID,
            ContainmentScenario.MERGE_EXISTING,
            "containment-journal-sha256:" + "f" * 64,
            None,
            ScenarioCleanupStatus.SUCCEEDED,
            False,
            (),
        )
        self.calls: list[str] = []
        self.mutations: list[str] = []
        self.effects = {} if effects is None else dict(effects)

    def _effects(self, name: str):
        supplied = self.effects.get(name)
        if supplied is not None:
            return supplied
        scenario_root = EFFECT_ROOT / "scenario"
        defaults = {
            "prepare": containment_service.ContainmentEffects(
                written_paths=(EFFECT_ROOT / "prepare-root",),
                child_mutation_roots=(EFFECT_ROOT / "prepare-root",),
            ),
            "arm": containment_service.ContainmentEffects(
                written_paths=(
                    scenario_root / "before.json",
                    scenario_root / "journal.json",
                    scenario_root / "watch",
                ),
                child_mutation_roots=(scenario_root / "watch",),
                watcher_pid=42,
            ),
            "launch": containment_service.ContainmentEffects(
                written_paths=(
                    scenario_root / "journal.json",
                    scenario_root / "launch.json",
                ),
                mo2_pid=2468,
            ),
            "capture": containment_service.ContainmentEffects(
                written_paths=(
                    scenario_root / "watch",
                    scenario_root / "fixture",
                    scenario_root / "after.json",
                    scenario_root / "result.json",
                    scenario_root / "journal.json",
                ),
                child_mutation_roots=(
                    scenario_root / "watch",
                    scenario_root / "fixture",
                ),
            ),
            "recover": containment_service.ContainmentEffects(
                written_paths=(scenario_root / "recovery.json",),
            ),
            "adjudicate": containment_service.ContainmentEffects(
                written_paths=(EFFECT_ROOT / "decision.json",),
            ),
            "show-result": containment_service.ContainmentEffects(),
            "show-decision": containment_service.ContainmentEffects(),
        }
        return defaults[name]

    def _result(self, value, operation: str):
        return containment_service.ContainmentServiceResult(
            value,
            self._effects(operation),
        )

    def prepare_run(self, source, artifact, steam, validation):
        self.mutations.append("prepare")
        self.calls.append("prepare")
        self.prepared = (source, artifact, steam, validation)
        return self._result(RUN_ID, "prepare")

    def arm_scenario(self, validation, run_id, scenario):
        self.mutations.append("arm")
        self.calls.append("arm")
        return self._result(
            SimpleNamespace(mo2_pid=None, stage_root=r"C:\contained-stage"),
            "arm",
        )

    def launch_scenario(self, validation, run_id, scenario):
        self.mutations.append("launch")
        self.calls.append("launch")
        return self._result(SimpleNamespace(mo2_pid=2468), "launch")

    def capture_scenario(self, validation, run_id, scenario):
        self.mutations.append("capture")
        self.calls.append("capture")
        return self._result(
            replace(self.scenario_result, run_id=run_id, scenario=scenario),
            "capture",
        )

    def recover_scenario(self, validation, run_id, scenario):
        self.mutations.append("recover")
        self.calls.append("recover")
        return self._result(
            replace(self.recovery, run_id=run_id, scenario=scenario),
            "recover",
        )

    def adjudicate_run(self, validation, run_id):
        self.mutations.append("adjudicate")
        self.calls.append("adjudicate")
        if self.decision is None:
            return self._result(
                CapabilityDecision(
                    1,
                    run_id,
                    "isolated-low-integrity-junction-projection-v1",
                    CapabilityVerdict.SUPPORTED,
                    (),
                    (),
                ),
                "adjudicate",
            )
        return self._result(replace(self.decision, run_id=run_id), "adjudicate")

    def load_result(self, validation, run_id, scenario):
        self.calls.append("show-result")
        return self._result(
            replace(self.scenario_result, run_id=run_id, scenario=scenario),
            "show-result",
        )

    def load_decision(self, validation, run_id):
        self.calls.append("show-decision")
        if self.decision is None:
            raise ContainmentStoreNotFound("decision is absent")
        return self._result(
            replace(self.decision, run_id=run_id),
            "show-decision",
        )


def prepare_args(*, output_format="text") -> list[str]:
    return [
        "prepare",
        "--source-workspace",
        r"C:\source",
        "--artifact",
        ARTIFACT_ID,
        "--steam-root",
        r"C:\steam",
        "--workspace",
        r"C:\workspace",
        "--format",
        output_format,
    ]


def run_args(command: str, *, output_format="text") -> list[str]:
    return [command, RUN_ID, "--workspace", r"C:\workspace", "--format", output_format]


def scenario_args(
    command: str = "validate",
    *,
    scenario="MergeExisting",
    output_format="text",
) -> list[str]:
    return [
        command,
        RUN_ID,
        scenario,
        "--workspace",
        r"C:\workspace",
        "--format",
        output_format,
    ]


def invoke_cli(argv: list[str], service: FakeService, *, reply="continue"):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cli.main(
        argv,
        stdout=stdout,
        stderr=stderr,
        service=service,
        input_func=lambda _prompt: reply,
    )
    return code, stdout.getvalue(), stderr.getvalue()


class ContainmentCliTests(unittest.TestCase):
    def test_show_against_absent_workspace_creates_nothing(self):
        with tempfile.TemporaryDirectory(prefix="modlab-show-absent-") as directory:
            workspace = Path(directory) / "absent-workspace"
            stdout = io.StringIO()
            stderr = io.StringIO()

            code = cli.main(
                [
                    "show",
                    RUN_ID,
                    "--workspace",
                    str(workspace),
                    "--format",
                    "json",
                ],
                stdout=stdout,
                stderr=stderr,
            )

            document = json.loads(stdout.getvalue())
            self.assertEqual(3, code)
            self.assertFalse(workspace.exists())
            self.assertEqual([], document["writtenPaths"])
            self.assertEqual("", stderr.getvalue())

    def test_malformed_live_show_is_exit_two_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="modlab-show-malformed-") as directory:
            workspace = Path(directory) / "workspace"
            validation = cli._validation_root(workspace)
            store = containment_service.ContainmentStore(validation)
            store.prepare_run_root(RUN_ID)
            run_root = store.evidence_run_path(RUN_ID)
            decision = run_root / "decision.json"
            decision.write_bytes(b"{malformed\n")
            before = tuple(
                (path.relative_to(workspace), path.read_bytes() if path.is_file() else None)
                for path in sorted(workspace.rglob("*"))
            )
            stdout = io.StringIO()

            code = cli.main(
                [
                    "show",
                    RUN_ID,
                    "--workspace",
                    str(workspace),
                    "--format",
                    "json",
                ],
                stdout=stdout,
                stderr=io.StringIO(),
            )

            document = json.loads(stdout.getvalue())
            after = tuple(
                (path.relative_to(workspace), path.read_bytes() if path.is_file() else None)
                for path in sorted(workspace.rglob("*"))
            )
            self.assertEqual(2, code)
            self.assertEqual([], document["writtenPaths"])
            self.assertEqual(before, after)

    def test_prepare_reports_disposable_validation_without_production_action(self):
        code, output, _ = invoke_cli(prepare_args(), FakeService())

        self.assertEqual(0, code)
        self.assertIn("Disposable validation only", output)
        self.assertIn("Production MO2 changes: none", output)
        self.assertIn("Written paths:", output)

    def test_prepare_maps_only_its_service_effect_receipt(self):
        exact = containment_service.ContainmentEffects(
            written_paths=(EFFECT_ROOT / "prepared-exact-root",),
            child_mutation_roots=(EFFECT_ROOT / "prepared-exact-root",),
        )

        code, output, _ = invoke_cli(
            prepare_args(output_format="json"),
            FakeService(effects={"prepare": exact}),
        )

        document = json.loads(output)
        self.assertEqual(0, code)
        self.assertEqual(
            [str(EFFECT_ROOT / "prepared-exact-root")],
            document["writtenPaths"],
        )

    def test_validate_prints_exact_visible_merge_instructions(self):
        code, output, _ = invoke_cli(scenario_args(), FakeService())

        self.assertEqual(0, code)
        self.assertIn("Select overwrite-probe.zip", output)
        self.assertIn("rename the target to Protected Existing", output)
        self.assertIn("choose Merge", output)
        self.assertIn("leave backup unchecked", output)
        self.assertIn("MO2 PID: 2468", output)

    def test_validate_prints_the_other_exact_visible_scenario_instructions(self):
        new_code, new_output, _ = invoke_cli(
            scenario_args(scenario="NewFolder"), FakeService()
        )
        replace_code, replace_output, _ = invoke_cli(
            scenario_args(scenario="ReplaceExisting"), FakeService()
        )
        fomod_code, fomod_output, _ = invoke_cli(
            scenario_args(scenario="FomodDependency"), FakeService()
        )

        self.assertEqual(0, new_code)
        self.assertIn("install new-folder.zip", new_output)
        self.assertIn("ModLab Spike New", new_output)
        self.assertEqual(0, replace_code)
        self.assertIn("choose Replace", replace_output)
        self.assertEqual(0, fomod_code)
        self.assertIn("install fomod-dependency.zip as ModLab Spike FOMOD", fomod_output)

    def test_validate_keeps_arm_launch_and_capture_in_one_controller(self):
        service = FakeService()

        code, _, _ = invoke_cli(scenario_args(), service)

        self.assertEqual(0, code)
        self.assertEqual(["arm", "launch", "capture"], service.calls)

    def test_validate_maps_only_exact_service_receipts_without_guesses(self):
        arm_path = EFFECT_ROOT / "only-arm.effect"
        launch_path = EFFECT_ROOT / "only-launch.effect"
        capture_path = EFFECT_ROOT / "only-capture.effect"
        service = FakeService(
            effects={
                "arm": containment_service.ContainmentEffects(
                    written_paths=(arm_path,),
                    watcher_pid=123,
                ),
                "launch": containment_service.ContainmentEffects(
                    written_paths=(launch_path,),
                    mo2_pid=456,
                ),
                "capture": containment_service.ContainmentEffects(
                    written_paths=(capture_path,),
                    source_changes=("receipt-source-change",),
                    game_changes=("receipt-game-change",),
                    production_mo2_changes=("receipt-production-change",),
                ),
            },
        )

        code, output, _ = invoke_cli(
            scenario_args(output_format="json"),
            service,
        )

        document = json.loads(output)
        self.assertEqual(0, code)
        self.assertEqual(
            [str(arm_path), str(launch_path), str(capture_path)],
            document["writtenPaths"],
        )
        self.assertEqual(
            ["Watcher PID: 123", "MO2 PID: 456"],
            document["launchedProcesses"],
        )
        self.assertEqual(["receipt-source-change"], document["sourceChanges"])
        self.assertEqual(["receipt-game-change"], document["gameChanges"])
        self.assertEqual(
            ["receipt-production-change"],
            document["productionMo2Changes"],
        )
        self.assertFalse(
            any(
                path.endswith(("before.json", "request.json", "outcome.json", "result.json"))
                for path in document["writtenPaths"]
            )
        )

    def test_validate_does_not_infer_processes_from_return_values(self):
        empty = containment_service.ContainmentEffects()
        service = FakeService(
            effects={"arm": empty, "launch": empty, "capture": empty},
        )

        code, output, _ = invoke_cli(
            scenario_args(output_format="json"),
            service,
        )

        document = json.loads(output)
        self.assertEqual(0, code)
        self.assertEqual([], document["launchedProcesses"])
        self.assertEqual([], document["writtenPaths"])

    def test_validate_refuses_noninteractive_stdin_before_arming(self):
        # Catches an EOF-only controller launching MO2 and losing its capture gate.
        service = FakeService()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(cli.sys, "stdin", io.StringIO("")):
            code = cli.main(
                scenario_args(scenario="NewFolder", output_format="json"),
                stdout=stdout,
                stderr=stderr,
                service=service,
            )

        document = json.loads(stdout.getvalue())
        self.assertEqual(3, code)
        self.assertEqual([], service.calls)
        self.assertEqual("Refused", document["state"])
        self.assertEqual([], document["writtenPaths"])
        self.assertTrue(
            any("interactive terminal" in reason for reason in document["reasons"])
        )

    def test_validate_distinguishes_proven_failure_from_incomplete(self):
        failed = FakeService(scenario_result=result(ScenarioOutcome.FAILED))
        incomplete = FakeService(scenario_result=result(ScenarioOutcome.INCOMPLETE))

        failed_code, _, _ = invoke_cli(scenario_args(), failed)
        incomplete_code, _, _ = invoke_cli(scenario_args(), incomplete)

        self.assertEqual(1, failed_code)
        self.assertEqual(3, incomplete_code)

    def test_validate_discloses_typed_result_evidence_and_all_confirmed_artifacts(self):
        changed = replace(
            result(ScenarioOutcome.FAILED),
            watcher_events=(
                WatcherEvent(1, "SourceMods", "Modified", "marker.txt"),
                WatcherEvent(2, "BoundedGame", "Modified", "Skyrim.esm"),
            ),
            production_backup_names=("Protected Existing_backup",),
        )

        capture_effects = containment_service.ContainmentEffects(
            written_paths=(EFFECT_ROOT / "scenario" / "result.json",),
            source_changes=("SourceMods: Modified marker.txt",),
            game_changes=("BoundedGame: Modified Skyrim.esm",),
            production_mo2_changes=(
                "Observed production backup: Protected Existing_backup",
            ),
        )
        code, output, _ = invoke_cli(
            scenario_args(output_format="json"),
            FakeService(
                scenario_result=changed,
                effects={"capture": capture_effects},
            ),
        )

        document = json.loads(output)
        self.assertEqual(1, code)
        self.assertTrue(any("SourceMods" in item for item in document["sourceChanges"]))
        self.assertTrue(any("BoundedGame" in item for item in document["gameChanges"]))
        self.assertTrue(any("Protected Existing_backup" in item for item in document["productionMo2Changes"]))
        self.assertEqual(
            list(
                map(
                    str,
                    containment_service.ContainmentEffects.merged(
                        FakeService()._effects("arm"),
                        FakeService()._effects("launch"),
                        capture_effects,
                    ).written_paths,
                )
            ),
            document["writtenPaths"],
        )

    def test_post_launch_capture_refusal_retains_pid_artifacts_and_containment_root(self):
        failed_path = EFFECT_ROOT / "capture-failed-after-write.json"
        service = FakeService()

        def capture_refusal(*_args):
            raise containment_service.ContainmentOperationError(
                "capture operation interrupted",
                effects=containment_service.ContainmentEffects(
                    written_paths=(failed_path,),
                    production_mo2_changes=("receipt-before-refusal",),
                ),
            )

        service.capture_scenario = capture_refusal
        code, output, _ = invoke_cli(scenario_args(output_format="json"), service)

        document = json.loads(output)
        self.assertEqual(3, code)
        self.assertEqual(RUN_ID, document["runId"])
        self.assertEqual("MergeExisting", document["scenario"])
        self.assertIn("Watcher PID: 42", document["launchedProcesses"])
        self.assertIn("MO2 PID: 2468", document["launchedProcesses"])
        self.assertIn(str(failed_path), document["writtenPaths"])
        self.assertEqual(
            ["receipt-before-refusal"],
            document["productionMo2Changes"],
        )

    def test_post_launch_capture_binding_refusal_retains_controller_context(self):
        service = FakeService()
        service.capture_scenario = lambda *_args: containment_service.ContainmentServiceResult(
            replace(
                result(),
                run_id="containment-run:" + "f" * 32,
            ),
            containment_service.ContainmentEffects(
                written_paths=(EFFECT_ROOT / "foreign-capture.json",),
            ),
        )

        code, output, _ = invoke_cli(scenario_args(output_format="json"), service)

        document = json.loads(output)
        self.assertEqual(3, code)
        self.assertEqual("MergeExisting", document["scenario"])
        self.assertIn("Watcher PID: 42", document["launchedProcesses"])
        self.assertIn("MO2 PID: 2468", document["launchedProcesses"])
        self.assertIn(
            str(EFFECT_ROOT / "foreign-capture.json"),
            document["writtenPaths"],
        )

    def test_adjudicate_never_defaults_incomplete_to_supported(self):
        service = FakeService(
            decision=CapabilityDecision(
                1,
                RUN_ID,
                "isolated-low-integrity-junction-projection-v1",
                CapabilityVerdict.INCOMPLETE,
                (),
                ("scenario-evidence-incomplete",),
            )
        )

        code, output, _ = invoke_cli(run_args("adjudicate"), service)

        self.assertEqual(3, code)
        self.assertIn("Verdict: Incomplete", output)

    def test_adjudicate_not_ready_is_exit_three_without_guessed_decision_write(self):
        service = FakeService()

        def refuse(*_args):
            raise containment_service.ContainmentDecisionNotReady(
                "four current results are required",
                effects=containment_service.ContainmentEffects(),
            )

        service.adjudicate_run = refuse
        code, output, _ = invoke_cli(
            run_args("adjudicate", output_format="json"),
            service,
        )

        document = json.loads(output)
        self.assertEqual(3, code)
        self.assertEqual([], document["writtenPaths"])
        self.assertIsNone(document["verdict"])

    def test_json_prepare_exposes_exact_run_id_and_complete_shape(self):
        code, output, _ = invoke_cli(prepare_args(output_format="json"), FakeService())

        self.assertEqual(0, code)
        document = json.loads(output)
        self.assertEqual(RUN_ID, document["runId"])
        self.assertEqual(
            {
                "command", "runId", "scenario", "state", "outcome", "verdict",
                "writtenPaths", "launchedProcesses", "sourceChanges", "gameChanges",
                "productionMo2Changes", "instructions", "reasons",
            },
            set(document),
        )
        self.assertIsNone(document["scenario"])
        self.assertEqual([], document["productionMo2Changes"])

    def test_json_validate_writes_one_final_object_to_stdout_and_guidance_to_stderr(self):
        code, stdout, stderr = invoke_cli(
            scenario_args(output_format="json"), FakeService()
        )

        self.assertEqual(0, code)
        self.assertIsInstance(json.loads(stdout), dict)
        self.assertNotIn("Select overwrite-probe.zip", stdout)
        self.assertIn("Select overwrite-probe.zip", stderr)

    def test_json_validate_never_sends_its_continue_prompt_to_stdout(self):
        service = FakeService()
        stdout = io.StringIO()
        stderr = io.StringIO()

        def simulated_input(prompt: str) -> str:
            if prompt:
                print(prompt, file=stdout)
            return "continue"

        code = cli.main(
            scenario_args(output_format="json"),
            stdout=stdout,
            stderr=stderr,
            service=service,
            input_func=simulated_input,
        )

        self.assertEqual(0, code)
        self.assertIsInstance(json.loads(stdout.getvalue()), dict)

    def test_show_is_read_only_and_returns_zero_for_failed_history(self):
        service = FakeService(scenario_result=result(ScenarioOutcome.FAILED))

        code, output, _ = invoke_cli(run_args("show"), service)

        self.assertEqual(0, code)
        self.assertIn("Outcome: Failed", output)
        self.assertEqual([], service.mutations)

    def test_show_displays_every_retained_scenario_history_and_decision(self):
        service = FakeService(
            decision=CapabilityDecision(
                1,
                RUN_ID,
                "isolated-low-integrity-junction-projection-v1",
                CapabilityVerdict.REJECTED,
                (),
                ("containment-breach",),
            )
        )
        results = {
            ContainmentScenario.NEW_FOLDER: replace(
                result(ScenarioOutcome.PASSED),
                scenario=ContainmentScenario.NEW_FOLDER,
            ),
            ContainmentScenario.MERGE_EXISTING: result(ScenarioOutcome.FAILED),
        }

        result_effects = {
            ContainmentScenario.NEW_FOLDER: containment_service.ContainmentEffects(
                source_changes=("new-folder-source",),
                production_mo2_changes=("new-folder-production",),
            ),
            ContainmentScenario.MERGE_EXISTING: containment_service.ContainmentEffects(
                source_changes=("merge-source",),
                game_changes=("merge-game",),
            ),
        }

        def load_result(_validation, _run_id, scenario):
            if scenario not in results:
                raise ContainmentStoreNotFound("result is absent")
            return containment_service.ContainmentServiceResult(
                results[scenario],
                result_effects[scenario],
            )

        service.load_result = load_result
        code, output, _ = invoke_cli(run_args("show", output_format="json"), service)

        document = json.loads(output)
        self.assertEqual(0, code)
        self.assertEqual("Failed", document["outcome"])
        self.assertEqual("Rejected", document["verdict"])
        self.assertIn("Scenario NewFolder: Passed", document["reasons"])
        self.assertIn("Scenario MergeExisting: Failed", document["reasons"])
        self.assertEqual(
            ["new-folder-source", "merge-source"],
            document["sourceChanges"],
        )
        self.assertEqual(["merge-game"], document["gameChanges"])
        self.assertEqual(
            ["new-folder-production"],
            document["productionMo2Changes"],
        )
        self.assertEqual([], document["writtenPaths"])

    def test_parse_time_json_error_writes_one_complete_json_response(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        code = cli.main(
            ["show", "--workspace", r"C:\workspace", "--format", "json"],
            stdout=stdout,
            stderr=stderr,
            service=FakeService(),
        )

        document = json.loads(stdout.getvalue())
        self.assertEqual(2, code)
        self.assertEqual("show", document["command"])
        self.assertEqual(
            {
                "command", "runId", "scenario", "state", "outcome", "verdict",
                "writtenPaths", "launchedProcesses", "sourceChanges", "gameChanges",
                "productionMo2Changes", "instructions", "reasons",
            },
            set(document),
        )
        self.assertEqual("", stderr.getvalue())

    def test_recover_success_returns_zero_without_upgrading_old_result(self):
        service = FakeService(
            scenario_result=result(ScenarioOutcome.INCOMPLETE),
            recovery=ScenarioRecovery(
                1,
                RUN_ID,
                ContainmentScenario.MERGE_EXISTING,
                "containment-journal-sha256:" + "f" * 64,
                "containment-result-sha256:" + "1" * 64,
                ScenarioCleanupStatus.SUCCEEDED,
                False,
                (),
            ),
        )

        code, output, _ = invoke_cli(scenario_args("recover"), service)

        self.assertEqual(0, code)
        self.assertIn("Cleanup: Succeeded", output)
        self.assertEqual(ScenarioOutcome.INCOMPLETE, service.scenario_result.outcome)

    def test_recover_maps_only_the_service_effect_receipt(self):
        path = EFFECT_ROOT / "exact-recovery-write.json"
        code, output, _ = invoke_cli(
            scenario_args("recover", output_format="json"),
            FakeService(
                effects={
                    "recover": containment_service.ContainmentEffects(
                        written_paths=(path,),
                    )
                }
            ),
        )

        document = json.loads(output)
        self.assertEqual(0, code)
        self.assertEqual([str(path)], document["writtenPaths"])

    def test_malformed_identifier_is_a_plain_exit_two_refusal(self):
        code, output, errors = invoke_cli(
            ["show", "containment-run:BAD", "--workspace", r"C:\workspace"],
            FakeService(),
        )

        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("run ID", errors)
        self.assertNotIn("Traceback", errors)

    def test_operational_invalid_transition_uses_exit_three_not_message_matching(self):
        service = FakeService()

        def invalid_transition(*_args):
            raise ContainmentStoreError("invalid transition")

        service.load_result = invalid_transition
        code, _, _ = invoke_cli(run_args("show"), service)

        self.assertEqual(3, code)

    def test_malformed_evidence_through_cli_uses_exit_two_and_one_json_response(self):
        service = FakeService()

        def malformed_decision(*_args):
            raise ContainmentStoreMalformedEvidence("decision bytes are not canonical")

        service.load_decision = malformed_decision
        code, output, errors = invoke_cli(
            run_args("show", output_format="json"),
            service,
        )

        document = json.loads(output)
        self.assertEqual(2, code)
        self.assertEqual("show", document["command"])
        self.assertIn("Safe refusal", document["reasons"][0])
        self.assertEqual("", errors)

    def test_nested_malformed_evidence_outranks_outer_not_ready(self):
        service = FakeService()

        def nested_failure(*_args):
            try:
                raise ContainmentStoreMalformedEvidence(
                    "nested result bytes are not canonical"
                )
            except ContainmentStoreMalformedEvidence as malformed:
                raise containment_service.ContainmentDecisionNotReady(
                    "decision not ready"
                ) from malformed

        service.load_decision = nested_failure
        code, output, errors = invoke_cli(
            run_args("show", output_format="json"),
            service,
        )

        self.assertEqual(2, code)
        self.assertEqual("show", json.loads(output)["command"])
        self.assertEqual("", errors)

    def test_suppressed_malformed_context_still_outranks_outer_not_ready(self):
        service = FakeService()

        def nested_failure(*_args):
            try:
                raise ContainmentStoreMalformedEvidence(
                    "suppressed nested result bytes are not canonical"
                )
            except ContainmentStoreMalformedEvidence:
                raise containment_service.ContainmentDecisionNotReady(
                    "decision not ready"
                ) from None

        service.load_decision = nested_failure
        code, output, errors = invoke_cli(
            run_args("show", output_format="json"),
            service,
        )

        self.assertEqual(2, code)
        self.assertEqual("show", json.loads(output)["command"])
        self.assertEqual("", errors)

    def test_malformed_prepare_result_preserves_effect_receipt_as_operational_refusal(self):
        service = FakeService()
        changed = EFFECT_ROOT / "prepared-before-invalid-result.json"
        effects = containment_service.ContainmentEffects(
            written_paths=(changed,),
        )
        service.prepare_run = lambda *_args: containment_service.ContainmentServiceResult(
            "not-a-run-id",
            effects,
        )

        code, output, errors = invoke_cli(
            prepare_args(output_format="json"),
            service,
        )

        document = json.loads(output)
        self.assertEqual(3, code)
        self.assertEqual([str(changed)], document["writtenPaths"])
        self.assertIn("Safe refusal", document["reasons"][0])
        self.assertEqual("", errors)


if __name__ == "__main__":
    unittest.main()
