"""Opt-in, foreground operator boundary for MO2 containment validation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Callable, Protocol, TextIO, TypeVar

from modlab.workspace import workspace_layout

from .mo2_containment_model import (
    CapabilityDecision,
    CapabilityVerdict,
    ContainmentScenario,
    ScenarioCleanupStatus,
    ScenarioOutcome,
    ScenarioRecovery,
    ScenarioResult,
)
from .mo2_containment_service import (
    ContainmentDecisionNotReady,
    ContainmentEffects,
    ContainmentServiceResult,
    ContainmentServiceError,
    adjudicate_run,
    arm_scenario,
    capture_scenario,
    launch_scenario,
    load_decision as load_service_decision,
    load_result as load_service_result,
    prepare_run,
    recover_scenario,
)
from .mo2_containment_store import (
    ContainmentStoreError,
    ContainmentStoreMalformedEvidence,
    ContainmentStoreNotFound,
)


_ARTIFACT_ID = re.compile(r"^archive-sha256:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^containment-run:([0-9a-f]{32})$")
_V = TypeVar("_V")


class CliInputError(ValueError):
    """A public command argument is not an exact containment value."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError(message)


class _ValidationControllerRefusal(RuntimeError):
    def __init__(
        self,
        run_id: str,
        scenario: ContainmentScenario,
        effects: ContainmentEffects,
        cause: BaseException,
    ) -> None:
        super().__init__(str(cause))
        self.run_id = run_id
        self.scenario = scenario
        self.effects = effects
        self.cause = cause


class _Service(Protocol):
    def prepare_run(
        self, source: Path, artifact: str, steam: Path, validation: Path
    ) -> ContainmentServiceResult[str]: ...
    def arm_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[object]: ...
    def launch_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[object]: ...
    def capture_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[ScenarioResult]: ...
    def recover_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[ScenarioRecovery]: ...
    def adjudicate_run(
        self, validation: Path, run_id: str
    ) -> ContainmentServiceResult[CapabilityDecision]: ...
    def load_result(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[ScenarioResult]: ...
    def load_decision(
        self, validation: Path, run_id: str
    ) -> ContainmentServiceResult[CapabilityDecision]: ...


@dataclass(frozen=True)
class _LiveService:
    def prepare_run(
        self, source: Path, artifact: str, steam: Path, validation: Path
    ) -> ContainmentServiceResult[str]:
        return prepare_run(source, artifact, steam, validation)

    def arm_scenario(self, validation: Path, run_id: str, scenario: ContainmentScenario):
        return arm_scenario(validation, run_id, scenario)

    def launch_scenario(self, validation: Path, run_id: str, scenario: ContainmentScenario):
        return launch_scenario(validation, run_id, scenario)

    def capture_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[ScenarioResult]:
        return capture_scenario(validation, run_id, scenario)

    def recover_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[ScenarioRecovery]:
        return recover_scenario(validation, run_id, scenario)

    def adjudicate_run(
        self, validation: Path, run_id: str
    ) -> ContainmentServiceResult[CapabilityDecision]:
        return adjudicate_run(validation, run_id)

    def load_result(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ContainmentServiceResult[ScenarioResult]:
        return load_service_result(validation, run_id, scenario)

    def load_decision(
        self, validation: Path, run_id: str
    ) -> ContainmentServiceResult[CapabilityDecision]:
        return load_service_decision(validation, run_id)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m modlab.validation.mo2_containment_cli",
        description="Run disposable MO2 pre-write containment validation only.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-workspace", required=True, type=Path)
    prepare.add_argument("--artifact", required=True)
    prepare.add_argument("--steam-root", required=True, type=Path)
    prepare.add_argument("--workspace", required=True, type=Path)
    prepare.add_argument("--format", choices=("text", "json"), default="text")

    validate = commands.add_parser("validate")
    validate.add_argument("run_id")
    validate.add_argument("scenario")
    validate.add_argument("--workspace", required=True, type=Path)
    validate.add_argument("--format", choices=("text", "json"), default="text")

    recover = commands.add_parser("recover")
    recover.add_argument("run_id")
    recover.add_argument("scenario")
    recover.add_argument("--workspace", required=True, type=Path)
    recover.add_argument("--format", choices=("text", "json"), default="text")

    for name in ("show", "adjudicate"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        command.add_argument("--workspace", required=True, type=Path)
        command.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _validation_root(workspace: Path) -> Path:
    return workspace_layout(Path(workspace).expanduser().absolute()).mo2_containment_validation


def _require_artifact_id(value: str) -> str:
    if _ARTIFACT_ID.fullmatch(value) is None:
        raise CliInputError("artifact ID must use archive-sha256:<64 lowercase hex characters>")
    return value


def _require_run_id(value: str) -> str:
    if _RUN_ID.fullmatch(value) is None:
        raise CliInputError("run ID must use containment-run:<32 lowercase hex characters>")
    return value


def _require_scenario(value: str) -> ContainmentScenario:
    try:
        return ContainmentScenario(value)
    except ValueError as error:
        choices = ", ".join(scenario.value for scenario in ContainmentScenario)
        raise CliInputError(f"scenario must be one of: {choices}") from error


def _instructions(scenario: ContainmentScenario) -> tuple[str, ...]:
    if scenario is ContainmentScenario.NEW_FOLDER:
        return (
            "In the disposable MO2, install new-folder.zip.",
            "Leave or enter ModLab Spike New, then complete it.",
            "Close MO2 normally.",
        )
    if scenario is ContainmentScenario.MERGE_EXISTING:
        return (
            "In the disposable MO2, Select overwrite-probe.zip.",
            "When prompted, rename the target to Protected Existing.",
            "choose Merge and leave backup unchecked.",
            "Acknowledge any access-denied or cancel result, then close MO2 normally.",
        )
    if scenario is ContainmentScenario.REPLACE_EXISTING:
        return (
            "In the disposable MO2, Select overwrite-probe.zip.",
            "When prompted, rename the target to Protected Existing.",
            "choose Replace and leave backup unchecked.",
            "Acknowledge any access-denied or cancel result, then close MO2 normally.",
        )
    return (
        "In the disposable MO2, install fomod-dependency.zip as ModLab Spike FOMOD.",
        "Complete the normal FOMOD installer.",
        "Close MO2 normally.",
    )


def _response(
    command: str,
    *,
    run_id: str | None = None,
    scenario: ContainmentScenario | None = None,
    state: str | None = None,
    outcome: str | None = None,
    verdict: str | None = None,
    written_paths: tuple[Path, ...] = (),
    launched_processes: tuple[str, ...] = (),
    source_changes: tuple[str, ...] = (),
    game_changes: tuple[str, ...] = (),
    production_mo2_changes: tuple[str, ...] = (),
    instructions: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "command": command,
        "runId": run_id,
        "scenario": None if scenario is None else scenario.value,
        "state": state,
        "outcome": outcome,
        "verdict": verdict,
        "writtenPaths": [str(path) for path in written_paths],
        "launchedProcesses": list(launched_processes),
        "sourceChanges": list(source_changes),
        "gameChanges": list(game_changes),
        "productionMo2Changes": list(production_mo2_changes),
        "instructions": list(instructions),
        "reasons": list(reasons),
    }


def _emit(value: dict[str, object], output: TextIO, output_format: str) -> None:
    if output_format == "json":
        output.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        output.write("\n")
        return
    labels = (
        ("Written paths", "writtenPaths"),
        ("Launched processes", "launchedProcesses"),
        ("Source changes", "sourceChanges"),
        ("Game changes", "gameChanges"),
        ("Production MO2 changes", "productionMo2Changes"),
    )
    print("Disposable validation only. Production installation remains disabled.", file=output)
    print(f"Command: {value['command']}", file=output)
    if value["runId"] is not None:
        print(f"Run ID: {value['runId']}", file=output)
    if value["scenario"] is not None:
        print(f"Scenario: {value['scenario']}", file=output)
    if value["state"] is not None:
        label = "Cleanup" if value["command"] == "recover" else "State"
        print(f"{label}: {value['state']}", file=output)
    if value["outcome"] is not None:
        print(f"Outcome: {value['outcome']}", file=output)
    if value["verdict"] is not None:
        print(f"Verdict: {value['verdict']}", file=output)
    for label, key in labels:
        rows = value[key]
        print(f"{label}: {'none' if not rows else ''}", file=output)
        for row in rows:
            print(f"  - {row}", file=output)
    if value["instructions"]:
        print("Instructions:", file=output)
        for index, instruction in enumerate(value["instructions"], 1):
            print(f"  {index}. {instruction}", file=output)
    if value["reasons"]:
        print("Reasons:", file=output)
        for reason in value["reasons"]:
            print(f"  - {reason}", file=output)


def _exit_for_outcome(outcome: ScenarioOutcome) -> int:
    return 0 if outcome is ScenarioOutcome.PASSED else 1 if outcome is ScenarioOutcome.FAILED else 3


def _exit_for_verdict(verdict: CapabilityVerdict) -> int:
    return 0 if verdict is CapabilityVerdict.SUPPORTED else 1 if verdict is CapabilityVerdict.REJECTED else 3


def _exit_for_operational_error(error: BaseException) -> int:
    """Keep malformed retained evidence distinct from an uncertain live state."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ContainmentDecisionNotReady):
            return 3
        if isinstance(current, ContainmentStoreMalformedEvidence):
            return 2
        cause = getattr(current, "cause", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(current.__cause__, BaseException):
            pending.append(current.__cause__)
    return 3


def _require_receipt(value: object) -> ContainmentServiceResult[_V]:
    if not isinstance(value, ContainmentServiceResult) or not isinstance(
        value.effects,
        ContainmentEffects,
    ):
        raise ContainmentServiceError(
            "containment service returned a value without an exact effect receipt"
        )
    return value


def _error_effects(error: BaseException) -> ContainmentEffects:
    effects = getattr(error, "effects", None)
    return effects if isinstance(effects, ContainmentEffects) else ContainmentEffects()


def _effect_fields(effects: ContainmentEffects) -> dict[str, tuple[object, ...]]:
    return {
        "written_paths": effects.written_paths,
        "launched_processes": effects.launched_processes,
        "source_changes": effects.source_changes,
        "game_changes": effects.game_changes,
        "production_mo2_changes": effects.production_mo2_changes,
    }


def _progress_response(error: _ValidationControllerRefusal) -> dict[str, object]:
    return _response(
        "validate",
        run_id=error.run_id,
        scenario=error.scenario,
        state="RecoveryRequired",
        reasons=(f"Safe refusal: {error.cause}",),
        **_effect_fields(error.effects),
    )


def _prepare(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    artifact = _require_artifact_id(args.artifact)
    validation = _validation_root(args.workspace)
    receipt = _require_receipt(
        service.prepare_run(
            args.source_workspace,
            artifact,
            args.steam_root,
            validation,
        )
    )
    run_id = receipt.value
    _require_run_id(run_id)
    return 0, _response(
        "prepare",
        run_id=run_id,
        state="Prepared",
        reasons=("Disposable validation fixtures were prepared; production MO2 changes: none.",),
        **_effect_fields(receipt.effects),
    )


def _validate(
    args: argparse.Namespace,
    service: _Service,
    guidance: TextIO,
    input_func: Callable[[str], str],
) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    scenario = _require_scenario(args.scenario)
    validation = _validation_root(args.workspace)
    effects = ContainmentEffects()
    if input_func is input:
        try:
            interactive = bool(sys.stdin.isatty())
        except (AttributeError, OSError):
            interactive = False
        if not interactive:
            return 3, _response(
                "validate",
                run_id=run_id,
                scenario=scenario,
                state="Refused",
                reasons=(
                    "Safe refusal: validate requires an interactive terminal "
                    "before arming or launching MO2",
                ),
            )
    try:
        armed = _require_receipt(service.arm_scenario(validation, run_id, scenario))
    except (ContainmentServiceError, ContainmentStoreError, OSError, RuntimeError) as error:
        raise _ValidationControllerRefusal(
            run_id,
            scenario,
            ContainmentEffects.merged(effects, _error_effects(error)),
            error,
        ) from error
    effects = ContainmentEffects.merged(effects, armed.effects)
    try:
        launched = _require_receipt(
            service.launch_scenario(validation, run_id, scenario)
        )
    except (ContainmentServiceError, ContainmentStoreError, OSError, RuntimeError) as error:
        raise _ValidationControllerRefusal(
            run_id,
            scenario,
            ContainmentEffects.merged(effects, _error_effects(error)),
            error,
        ) from error
    effects = ContainmentEffects.merged(effects, launched.effects)
    pid = launched.effects.mo2_pid
    procedure = _instructions(scenario)
    published_instructions = (
        ("Interactive operator procedure was displayed on stderr.",)
        if args.format == "json"
        else procedure
    )
    for instruction in procedure:
        print(instruction, file=guidance)
    if isinstance(pid, int) and pid > 0:
        print(f"MO2 PID: {pid}", file=guidance)
    print("After MO2 is closed normally, type continue to capture immutable evidence.", file=guidance)
    print("Type continue after MO2 is closed normally:", file=guidance)
    try:
        confirmation = input_func("")
    except (EOFError, KeyboardInterrupt):
        confirmation = ""
    if confirmation.strip().casefold() != "continue":
        return 3, _response(
            "validate",
            run_id=run_id,
            scenario=scenario,
            state="Launched",
            instructions=published_instructions,
            reasons=("Capture was not continued; recover performs cleanup only.",),
            **_effect_fields(effects),
        )
    try:
        captured = _require_receipt(
            service.capture_scenario(validation, run_id, scenario)
        )
        effects = ContainmentEffects.merged(effects, captured.effects)
        result = captured.value
        if (
            not isinstance(result, ScenarioResult)
            or result.run_id != run_id
            or result.scenario is not scenario
        ):
            raise ContainmentServiceError(
                "capture returned evidence bound to another scenario",
                effects=effects,
            )
    except (ContainmentServiceError, ContainmentStoreError, OSError, RuntimeError) as error:
        raise _ValidationControllerRefusal(
            run_id,
            scenario,
            ContainmentEffects.merged(effects, _error_effects(error)),
            error,
        ) from error
    return _exit_for_outcome(result.outcome), _response(
        "validate",
        run_id=run_id,
        scenario=scenario,
        state="Captured",
        outcome=result.outcome.value,
        instructions=published_instructions,
        reasons=result.reasons,
        **_effect_fields(effects),
    )


def _recover(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    scenario = _require_scenario(args.scenario)
    validation = _validation_root(args.workspace)
    receipt = _require_receipt(
        service.recover_scenario(validation, run_id, scenario)
    )
    recovery = receipt.value
    if (
        not isinstance(recovery, ScenarioRecovery)
        or recovery.run_id != run_id
        or recovery.scenario is not scenario
    ):
        raise ContainmentServiceError(
            "recovery returned evidence bound to another scenario",
            effects=receipt.effects,
        )
    code = 0 if recovery.cleanup_status is ScenarioCleanupStatus.SUCCEEDED else 3
    return code, _response(
        "recover",
        run_id=run_id,
        scenario=scenario,
        state=recovery.cleanup_status.value,
        reasons=recovery.blockers,
        **_effect_fields(receipt.effects),
    )


def _show(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    validation = _validation_root(args.workspace)
    receipts: list[ContainmentEffects] = []
    try:
        decision_receipt = _require_receipt(
            service.load_decision(validation, run_id)
        )
    except ContainmentStoreNotFound:
        decision = None
    else:
        decision = decision_receipt.value
        if not isinstance(decision, CapabilityDecision) or decision.run_id != run_id:
            raise ContainmentServiceError(
                "loaded decision is bound to another run",
                effects=decision_receipt.effects,
            )
        receipts.append(decision_receipt.effects)
    results: list[ScenarioResult] = []
    for scenario in ContainmentScenario:
        try:
            result_receipt = _require_receipt(
                service.load_result(validation, run_id, scenario)
            )
        except ContainmentStoreNotFound:
            continue
        result = result_receipt.value
        if (
            not isinstance(result, ScenarioResult)
            or result.run_id != run_id
            or result.scenario is not scenario
        ):
            raise ContainmentServiceError(
                "loaded result is bound to another scenario",
                effects=ContainmentEffects.merged(
                    *receipts,
                    result_receipt.effects,
                ),
            )
        results.append(result)
        receipts.append(result_receipt.effects)
    if not results and decision is None:
        raise ContainmentStoreNotFound("no containment result or decision exists for this run")
    rank = {ScenarioOutcome.PASSED: 0, ScenarioOutcome.INCOMPLETE: 1, ScenarioOutcome.FAILED: 2}
    selected = max(results, key=lambda value: rank[value.outcome]) if results else None
    reasons: list[str] = []
    for result in results:
        reasons.append(f"Scenario {result.scenario.value}: {result.outcome.value}")
        reasons.extend(f"{result.scenario.value}: {reason}" for reason in result.reasons)
    if decision is not None:
        reasons.extend(f"Decision: {reason}" for reason in decision.reasons)
    effects = ContainmentEffects.merged(*receipts)
    return 0, _response(
        "show",
        run_id=run_id,
        scenario=None if selected is None else selected.scenario,
        state=None if selected is None else "Captured",
        outcome=None if selected is None else selected.outcome.value,
        verdict=None if decision is None else decision.verdict.value,
        reasons=tuple(reasons),
        **_effect_fields(effects),
    )


def _adjudicate(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    validation = _validation_root(args.workspace)
    receipt = _require_receipt(service.adjudicate_run(validation, run_id))
    decision = receipt.value
    if not isinstance(decision, CapabilityDecision) or decision.run_id != run_id:
        raise ContainmentServiceError(
            "adjudication returned a decision bound to another run",
            effects=receipt.effects,
        )
    return _exit_for_verdict(decision.verdict), _response(
        "adjudicate",
        run_id=run_id,
        verdict=decision.verdict.value,
        reasons=decision.reasons,
        **_effect_fields(receipt.effects),
    )


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    service: _Service | None = None,
    input_func: Callable[[str], str] = input,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except CliInputError as error:
        raw = sys.argv[1:] if argv is None else argv
        command = next((item for item in raw if item in {"prepare", "validate", "recover", "show", "adjudicate"}), "unknown")
        json_requested = any(
            item == "--format=json"
            or (item == "--format" and index + 1 < len(raw) and raw[index + 1] == "json")
            for index, item in enumerate(raw)
        )
        if json_requested:
            _emit(_response(command, reasons=(str(error),)), output, "json")
            return 2
        print(f"Containment validation argument error: {error}", file=errors)
        return 2
    active_service: _Service = _LiveService() if service is None else service
    guidance = errors if args.format == "json" else output
    try:
        if args.command == "prepare":
            code, value = _prepare(args, active_service)
        elif args.command == "validate":
            code, value = _validate(args, active_service, guidance, input_func)
        elif args.command == "recover":
            code, value = _recover(args, active_service)
        elif args.command == "show":
            code, value = _show(args, active_service)
        else:
            code, value = _adjudicate(args, active_service)
    except CliInputError as error:
        if args.format == "json":
            _emit(_response(args.command, reasons=(str(error),)), output, "json")
        else:
            print(f"Containment validation argument error: {error}", file=errors)
        return 2
    except _ValidationControllerRefusal as error:
        _emit(_progress_response(error), output, args.format)
        return _exit_for_operational_error(error.cause)
    except (ContainmentServiceError, ContainmentStoreError, OSError, RuntimeError) as error:
        raw_scenario = getattr(args, "scenario", None)
        try:
            error_scenario = (
                None
                if raw_scenario is None
                else ContainmentScenario(raw_scenario)
            )
        except (TypeError, ValueError):
            error_scenario = None
        refusal = _response(
            args.command,
            run_id=getattr(args, "run_id", None),
            scenario=error_scenario,
            state="RecoveryRequired",
            reasons=(f"Safe refusal: {error}",),
            **_effect_fields(_error_effects(error)),
        )
        _emit(refusal, output, args.format)
        return _exit_for_operational_error(error)
    _emit(value, output, args.format)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
