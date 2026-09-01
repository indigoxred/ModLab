import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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
)
from modlab.validation import mo2_containment_cli as cli
from modlab.validation.mo2_containment_store import (
    ContainmentStoreError,
    ContainmentStoreNotFound,
)


RUN_ID = "containment-run:" + "a" * 32
ARTIFACT_ID = "archive-sha256:" + "b" * 64


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

    def prepare_run(self, source, artifact, steam, validation):
        self.mutations.append("prepare")
        self.calls.append("prepare")
        self.prepared = (source, artifact, steam, validation)
        return RUN_ID

    def arm_scenario(self, validation, run_id, scenario):
        self.mutations.append("arm")
        self.calls.append("arm")
        return SimpleNamespace(mo2_pid=None)

    def launch_scenario(self, validation, run_id, scenario):
        self.mutations.append("launch")
        self.calls.append("launch")
        return SimpleNamespace(mo2_pid=2468)

    def capture_scenario(self, validation, run_id, scenario):
        self.mutations.append("capture")
        self.calls.append("capture")
        return replace(self.scenario_result, run_id=run_id, scenario=scenario)

    def recover_scenario(self, validation, run_id, scenario):
        self.mutations.append("recover")
        self.calls.append("recover")
        return replace(self.recovery, run_id=run_id, scenario=scenario)

    def adjudicate_run(self, validation, run_id):
        self.mutations.append("adjudicate")
        self.calls.append("adjudicate")
        if self.decision is None:
            return CapabilityDecision(
                1,
                run_id,
                "isolated-low-integrity-junction-projection-v1",
                CapabilityVerdict.SUPPORTED,
                (),
                (),
            )
        return replace(self.decision, run_id=run_id)

    def load_result(self, validation, run_id, scenario):
        self.calls.append("show-result")
        return replace(self.scenario_result, run_id=run_id, scenario=scenario)

    def load_decision(self, validation, run_id):
        self.calls.append("show-decision")
        if self.decision is None:
            raise ContainmentStoreNotFound("decision is absent")
        return replace(self.decision, run_id=run_id)


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
    def test_prepare_reports_disposable_validation_without_production_action(self):
        code, output, _ = invoke_cli(prepare_args(), FakeService())

        self.assertEqual(0, code)
        self.assertIn("Disposable validation only", output)
        self.assertIn("Production MO2 changes: none", output)
        self.assertIn("Written paths:", output)

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

    def test_validate_distinguishes_proven_failure_from_incomplete(self):
        failed = FakeService(scenario_result=result(ScenarioOutcome.FAILED))
        incomplete = FakeService(scenario_result=result(ScenarioOutcome.INCOMPLETE))

        failed_code, _, _ = invoke_cli(scenario_args(), failed)
        incomplete_code, _, _ = invoke_cli(scenario_args(), incomplete)

        self.assertEqual(1, failed_code)
        self.assertEqual(3, incomplete_code)

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

    def test_malformed_identifier_is_a_plain_exit_two_refusal(self):
        code, output, errors = invoke_cli(
            ["show", "containment-run:BAD", "--workspace", r"C:\workspace"],
            FakeService(),
        )

        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("run ID", errors)
        self.assertNotIn("Traceback", errors)

    def test_malformed_stored_schema_uses_exit_two_not_safe_refusal(self):
        service = FakeService()

        def malformed_result(*_args):
            raise ContainmentStoreError("stored scenario result is invalid")

        service.load_result = malformed_result
        code, _, _ = invoke_cli(run_args("show"), service)

        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
