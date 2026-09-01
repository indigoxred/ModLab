"""Opt-in, foreground operator boundary for MO2 containment validation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Callable, Protocol, TextIO

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
    ContainmentServiceError,
    adjudicate_run,
    arm_scenario,
    capture_scenario,
    launch_scenario,
    prepare_run,
    recover_scenario,
)
from .mo2_containment_store import (
    ContainmentStore,
    ContainmentStoreError,
    ContainmentStoreNotFound,
)


_ARTIFACT_ID = re.compile(r"^archive-sha256:[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^containment-run:([0-9a-f]{32})$")


class CliInputError(ValueError):
    """A public command argument is not an exact containment value."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError(message)


class _Service(Protocol):
    def prepare_run(self, source: Path, artifact: str, steam: Path, validation: Path) -> str: ...
    def arm_scenario(self, validation: Path, run_id: str, scenario: ContainmentScenario): ...
    def launch_scenario(self, validation: Path, run_id: str, scenario: ContainmentScenario): ...
    def capture_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ScenarioResult: ...
    def recover_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ScenarioRecovery: ...
    def adjudicate_run(self, validation: Path, run_id: str) -> CapabilityDecision: ...
    def load_result(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ScenarioResult: ...
    def load_decision(self, validation: Path, run_id: str) -> CapabilityDecision: ...


@dataclass(frozen=True)
class _LiveService:
    def prepare_run(self, source: Path, artifact: str, steam: Path, validation: Path) -> str:
        return prepare_run(source, artifact, steam, validation)

    def arm_scenario(self, validation: Path, run_id: str, scenario: ContainmentScenario):
        return arm_scenario(validation, run_id, scenario)

    def launch_scenario(self, validation: Path, run_id: str, scenario: ContainmentScenario):
        return launch_scenario(validation, run_id, scenario)

    def capture_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ScenarioResult:
        return capture_scenario(validation, run_id, scenario)

    def recover_scenario(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ScenarioRecovery:
        return recover_scenario(validation, run_id, scenario)

    def adjudicate_run(self, validation: Path, run_id: str) -> CapabilityDecision:
        return adjudicate_run(validation, run_id)

    def load_result(
        self, validation: Path, run_id: str, scenario: ContainmentScenario
    ) -> ScenarioResult:
        return ContainmentStore(validation).load_result(run_id, scenario)

    def load_decision(self, validation: Path, run_id: str) -> CapabilityDecision:
        return ContainmentStore(validation).load_decision(run_id)


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


def _run_root(validation: Path, run_id: str) -> Path:
    return validation / run_id.split(":", 1)[1]


def _scenario_path(validation: Path, run_id: str, scenario: ContainmentScenario) -> Path:
    return _run_root(validation, run_id) / "scenarios" / scenario.value


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
    if isinstance(error, ContainmentStoreError):
        text = str(error).casefold()
        if any(token in text for token in ("invalid", "malformed", "canonical", "schema", "fields")):
            return 2
    return 3


def _prepare(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    artifact = _require_artifact_id(args.artifact)
    validation = _validation_root(args.workspace)
    run_id = service.prepare_run(args.source_workspace, artifact, args.steam_root, validation)
    _require_run_id(run_id)
    root = _run_root(validation, run_id)
    return 0, _response(
        "prepare",
        run_id=run_id,
        state="Prepared",
        written_paths=(root / "intent.json", root / "request.json"),
        reasons=("Disposable validation fixtures were prepared; production MO2 changes: none.",),
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
    service.arm_scenario(validation, run_id, scenario)
    launched = service.launch_scenario(validation, run_id, scenario)
    pid = getattr(launched, "mo2_pid", None)
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
            written_paths=(_scenario_path(validation, run_id, scenario) / "journal.json",),
            launched_processes=() if pid is None else (f"MO2 PID: {pid}",),
            instructions=published_instructions,
            reasons=("Capture was not continued; recover performs cleanup only.",),
        )
    result = service.capture_scenario(validation, run_id, scenario)
    if result.run_id != run_id or result.scenario is not scenario:
        raise ContainmentServiceError("capture returned evidence bound to another scenario")
    result_path = _scenario_path(validation, run_id, scenario) / "result.json"
    return _exit_for_outcome(result.outcome), _response(
        "validate",
        run_id=run_id,
        scenario=scenario,
        state="Captured",
        outcome=result.outcome.value,
        written_paths=(result_path,),
        launched_processes=() if result.mo2_process is None else (f"MO2 PID: {result.mo2_process.pid}",),
        instructions=published_instructions,
        reasons=result.reasons,
    )


def _recover(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    scenario = _require_scenario(args.scenario)
    validation = _validation_root(args.workspace)
    recovery = service.recover_scenario(validation, run_id, scenario)
    if recovery.run_id != run_id or recovery.scenario is not scenario:
        raise ContainmentServiceError("recovery returned evidence bound to another scenario")
    code = 0 if recovery.cleanup_status is ScenarioCleanupStatus.SUCCEEDED else 3
    return code, _response(
        "recover",
        run_id=run_id,
        scenario=scenario,
        state=recovery.cleanup_status.value,
        written_paths=(_scenario_path(validation, run_id, scenario) / "recovery.json",),
        reasons=recovery.blockers,
    )


def _show(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    validation = _validation_root(args.workspace)
    try:
        decision = service.load_decision(validation, run_id)
    except ContainmentStoreNotFound:
        decision = None
    if decision is not None:
        return 0, _response(
            "show",
            run_id=run_id,
            verdict=decision.verdict.value,
            reasons=decision.reasons,
        )
    for scenario in ContainmentScenario:
        try:
            result = service.load_result(validation, run_id, scenario)
        except ContainmentStoreNotFound:
            continue
        return 0, _response(
            "show",
            run_id=run_id,
            scenario=scenario,
            state="Captured",
            outcome=result.outcome.value,
            reasons=result.reasons,
        )
    raise ContainmentStoreNotFound("no containment result or decision exists for this run")


def _adjudicate(args: argparse.Namespace, service: _Service) -> tuple[int, dict[str, object]]:
    run_id = _require_run_id(args.run_id)
    validation = _validation_root(args.workspace)
    decision = service.adjudicate_run(validation, run_id)
    if decision.run_id != run_id:
        raise ContainmentServiceError("adjudication returned a decision bound to another run")
    return _exit_for_verdict(decision.verdict), _response(
        "adjudicate",
        run_id=run_id,
        verdict=decision.verdict.value,
        written_paths=(_run_root(validation, run_id) / "decision.json",),
        reasons=decision.reasons,
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
    except (ContainmentServiceError, ContainmentStoreError, OSError, RuntimeError) as error:
        refusal = _response(
            args.command,
            run_id=getattr(args, "run_id", None),
            state="RecoveryRequired",
            reasons=(f"Safe refusal: {error}",),
        )
        _emit(refusal, output, args.format)
        return _exit_for_operational_error(error)
    _emit(value, output, args.format)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
