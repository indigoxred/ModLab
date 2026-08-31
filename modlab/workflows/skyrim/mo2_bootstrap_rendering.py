"""Exact human and machine output contracts for verified MO2 setup."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from modlab.adapters.mo2.bootstrap_model import (
    BootstrapFailureResult,
    BootstrapDisposition,
    BootstrapJobState,
    BootstrapReceiptMode,
    RecoveryResult,
    SetupApplyResult,
    SetupPlanResult,
)
from modlab.adapters.mo2.bootstrap_serialization import (
    BootstrapFormatError,
    journal_to_dict,
    plan_to_dict,
    receipt_to_dict,
)


_TOP_LEVEL_KEYS = (
    "schemaVersion",
    "mo2Setup",
    "pathsWritten",
    "downloadsPerformed",
    "installationActionsPerformed",
    "managerChangesPerformed",
    "gameChangesPerformed",
    "programsLaunched",
)
_PLAN_OUTCOMES = {item.value for item in BootstrapDisposition}
_APPLY_OUTCOMES = {
    BootstrapReceiptMode.CREATED.value,
    BootstrapReceiptMode.ADOPTED.value,
    BootstrapDisposition.ALREADY_MANAGED.value,
}
_RECOVERY_OUTCOMES = {
    BootstrapJobState.VERIFIED.value,
    BootstrapJobState.RECOVERED.value,
}
_TITLES = {
    BootstrapDisposition.ALREADY_MANAGED.value: "Already managed",
    "RecoveryRequired": "Recovery required",
}


def plan_result_to_dict(result: SetupPlanResult) -> dict[str, object]:
    if not isinstance(result, SetupPlanResult):
        raise BootstrapFormatError("MO2 setup preview requires SetupPlanResult")
    plan = plan_to_dict(result.plan)
    if result.plan.disposition.value not in _PLAN_OUTCOMES:
        raise BootstrapFormatError("MO2 setup preview has an invalid outcome")
    if any((result.downloads, result.installations, result.manager_changes, result.game_changes)):
        raise BootstrapFormatError("MO2 setup preview cannot report performed changes")

    plan_path = _workspace_relative_path(
        result.plan_path,
        Path(result.plan.workspace_root),
        "retained plan path",
    )
    expected_plan_path = (
        "runtime/jobs/mo2-bootstrap/plans/"
        f"{result.plan.plan_id.removeprefix('bootstrap-plan-sha256:')}.json"
    )
    if plan_path != expected_plan_path:
        raise BootstrapFormatError("retained plan path does not match its plan ID")
    paths_written = tuple(
        _workspace_relative_path(path, Path(result.plan.workspace_root), "written path")
        for path in result.paths_written
    )
    _require_sorted_unique(paths_written, "pathsWritten")
    if paths_written not in {(), (plan_path,)}:
        raise BootstrapFormatError(
            "MO2 setup preview may report only its retained plan path"
        )
    if result.programs_launched != result.plan.programs_launched:
        raise BootstrapFormatError(
            "MO2 setup preview program evidence differs from its plan"
        )
    common = _common_result(
        paths_written=paths_written,
        downloads=result.downloads,
        installations=result.installations,
        manager_changes=result.manager_changes,
        game_changes=result.game_changes,
        programs_launched=result.programs_launched,
    )
    common["mo2Setup"] = {
        "outcome": result.plan.disposition.value,
        "planId": result.plan.plan_id,
        "planPath": plan_path,
        "plan": plan,
    }
    return _ordered_top_level(common)


def apply_result_to_dict(result: SetupApplyResult) -> dict[str, object]:
    if not isinstance(result, SetupApplyResult):
        raise BootstrapFormatError("MO2 setup apply output requires SetupApplyResult")
    if result.outcome not in _APPLY_OUTCOMES:
        raise BootstrapFormatError("MO2 setup apply has an invalid outcome")
    receipt = receipt_to_dict(result.receipt)
    if result.receipt.plan_id != result.plan_id:
        raise BootstrapFormatError("MO2 setup receipt belongs to another plan")

    journal = None
    job_id = None
    if result.journal is not None:
        journal = journal_to_dict(result.journal)
        job_id = result.journal.job_id
        if (
            result.journal.plan_id != result.plan_id
            or result.journal.job_id != result.receipt.job_id
            or result.journal.receipt_id != result.receipt.receipt_id
            or result.journal.state is not BootstrapJobState.VERIFIED
        ):
            raise BootstrapFormatError(
                "MO2 setup journal, plan, and receipt evidence disagree"
            )

    _require_apply_actions(result)
    _require_apply_record_paths(result)
    _require_bootstrap_programs(
        result.programs_launched,
        result.receipt.extractor.executable.path,
        include_extract=(result.outcome != BootstrapDisposition.ALREADY_MANAGED.value),
    )
    common = _common_result(
        paths_written=result.paths_written,
        downloads=result.downloads,
        installations=result.installations,
        manager_changes=result.manager_changes,
        game_changes=result.game_changes,
        programs_launched=result.programs_launched,
    )
    common["mo2Setup"] = {
        "outcome": result.outcome,
        "planId": result.plan_id,
        "jobId": job_id,
        "receiptId": result.receipt.receipt_id,
        "journal": journal,
        "receipt": receipt,
    }
    return _ordered_top_level(common)


def recovery_result_to_dict(result: RecoveryResult) -> dict[str, object]:
    if not isinstance(result, RecoveryResult):
        raise BootstrapFormatError("MO2 setup recovery output requires RecoveryResult")
    if result.outcome not in _RECOVERY_OUTCOMES:
        raise BootstrapFormatError("MO2 setup recovery has an invalid outcome")
    journal = journal_to_dict(result.journal)
    receipt = None if result.receipt is None else receipt_to_dict(result.receipt)
    if result.outcome != result.journal.state.value:
        raise BootstrapFormatError("MO2 setup recovery outcome and journal state disagree")
    if result.journal.state is BootstrapJobState.VERIFIED:
        if (
            result.receipt is None
            or result.journal.receipt_id != result.receipt.receipt_id
            or result.journal.job_id != result.receipt.job_id
            or result.journal.plan_id != result.receipt.plan_id
        ):
            raise BootstrapFormatError(
                "Verified MO2 recovery requires its exact receipt evidence"
            )
    elif result.receipt is not None:
        raise BootstrapFormatError("Recovered MO2 setup cannot report a receipt")
    _require_recovery_actions(result)
    _require_recovery_record_paths(result)
    _require_recovery_programs(result)
    common = _common_result(
        paths_written=result.paths_written,
        downloads=result.downloads,
        installations=result.installations,
        manager_changes=result.manager_changes,
        game_changes=result.game_changes,
        programs_launched=result.programs_launched,
    )
    common["mo2Setup"] = {
        "outcome": result.outcome,
        "planId": result.journal.plan_id,
        "jobId": result.journal.job_id,
        "receiptId": None if result.receipt is None else result.receipt.receipt_id,
        "journal": journal,
        "receipt": receipt,
    }
    return _ordered_top_level(common)


def plan_result_to_text(result: SetupPlanResult) -> str:
    value = plan_result_to_dict(result)
    setup = value["mo2Setup"]
    return _result_text(
        value,
        (
            _title(setup["outcome"]),
            f"Plan: {setup['planId']}",
            f"Retained plan: {setup['planPath']}",
        ),
    )


def apply_result_to_text(result: SetupApplyResult) -> str:
    value = apply_result_to_dict(result)
    setup = value["mo2Setup"]
    lines = [
        _title(setup["outcome"]),
        f"Plan: {setup['planId']}",
    ]
    if setup["jobId"] is not None:
        lines.append(f"Job: {setup['jobId']}")
    lines.append(f"Receipt: {setup['receiptId']}")
    return _result_text(value, tuple(lines))


def recovery_result_to_text(result: RecoveryResult) -> str:
    value = recovery_result_to_dict(result)
    setup = value["mo2Setup"]
    outcome = setup["outcome"]
    if outcome == BootstrapJobState.VERIFIED.value:
        assert result.receipt is not None
        outcome = result.receipt.mode.value
    lines = [
        _title(outcome),
        f"Job: {setup['jobId']}",
        f"Plan: {setup['planId']}",
    ]
    if setup["receiptId"] is not None:
        lines.append(f"Receipt: {setup['receiptId']}")
    return _result_text(value, tuple(lines))


def refusal_result_to_dict(
    code: str,
    message: str,
    failure: BootstrapFailureResult | None = None,
) -> dict[str, object]:
    if failure is not None and (
        failure.code != code or failure.message != message
    ):
        raise BootstrapFormatError("MO2 refusal and failure evidence disagree")
    if failure is not None:
        if failure.journal is not None and failure.job_id != failure.journal.job_id:
            raise BootstrapFormatError("MO2 failure job and journal evidence disagree")
        if failure.receipt is not None and (
            failure.plan_id != failure.receipt.plan_id
            or failure.job_id != failure.receipt.job_id
        ):
            raise BootstrapFormatError("MO2 failure receipt evidence disagrees")
    outcome = (
        failure.outcome
        if failure is not None
        else ("RecoveryRequired" if code == "recovery-required" else "Blocked")
    )
    if outcome not in {"Blocked", "RecoveryRequired"}:
        raise BootstrapFormatError("MO2 refusal has an invalid outcome")
    journal = (
        None
        if failure is None or failure.journal is None
        else journal_to_dict(failure.journal)
    )
    receipt = (
        None
        if failure is None or failure.receipt is None
        else receipt_to_dict(failure.receipt)
    )
    value = _common_result(
        paths_written=() if failure is None else failure.paths_written,
        downloads=() if failure is None else failure.downloads,
        installations=() if failure is None else failure.installations,
        manager_changes=() if failure is None else failure.manager_changes,
        game_changes=() if failure is None else failure.game_changes,
        programs_launched=() if failure is None else failure.programs_launched,
    )
    value["mo2Setup"] = {
        "outcome": outcome,
        "code": code,
        "message": message,
        "actionEvidenceComplete": (
            False if failure is None else failure.actions_complete
        ),
        "planId": None if failure is None else failure.plan_id,
        "jobId": (
            None if failure is None else failure.job_id
        ),
        "journal": journal,
        "receipt": receipt,
    }
    return _ordered_top_level(value)


def refusal_result_to_text(
    code: str,
    message: str,
    failure: BootstrapFailureResult | None = None,
) -> str:
    value = refusal_result_to_dict(code, message, failure)
    setup = value["mo2Setup"]
    heading = [_title(setup["outcome"]), message]
    if setup["jobId"] is not None:
        heading.append(f"Job: {setup['jobId']}")
    if setup["planId"] is not None:
        heading.append(f"Plan: {setup['planId']}")
    return _result_text(
        value,
        tuple(heading),
        actions_complete=bool(setup["actionEvidenceComplete"]),
    )


def _require_apply_actions(result: SetupApplyResult) -> None:
    if result.downloads or result.game_changes:
        raise BootstrapFormatError("MO2 setup cannot report downloads or game changes")
    if result.outcome == BootstrapReceiptMode.CREATED.value:
        if result.receipt.mode is not BootstrapReceiptMode.CREATED:
            raise BootstrapFormatError("Created output requires a Created receipt")
        if result.journal is None:
            raise BootstrapFormatError("Created output requires a verified job journal")
        if result.installations != ("portable-mo2-create",):
            raise BootstrapFormatError(
                "Created output requires exactly portable-mo2-create"
            )
        if not result.manager_changes:
            raise BootstrapFormatError("Created output requires exact manager changes")
    elif result.outcome == BootstrapReceiptMode.ADOPTED.value:
        if (
            result.receipt.mode is not BootstrapReceiptMode.ADOPTED
            or result.journal is None
            or result.installations
            or result.manager_changes
        ):
            raise BootstrapFormatError(
                "Adopted output cannot report installation or manager changes"
            )
    elif (
        result.journal is not None
        or result.paths_written
        or result.installations
        or result.manager_changes
    ):
        raise BootstrapFormatError("AlreadyManaged output must be a manager no-op")


def _require_apply_record_paths(result: SetupApplyResult) -> None:
    if result.journal is None:
        if result.paths_written:
            raise BootstrapFormatError("AlreadyManaged output cannot report written paths")
        return
    expected = _record_paths(result.journal.job_id, result.receipt.receipt_id)
    if result.paths_written != expected:
        raise BootstrapFormatError(
            "MO2 setup apply must report exactly its job journal and receipt paths"
        )


def _require_recovery_record_paths(result: RecoveryResult) -> None:
    allowed = set(_record_paths(
        result.journal.job_id,
        None if result.receipt is None else result.receipt.receipt_id,
    ))
    if any(path not in allowed for path in result.paths_written):
        raise BootstrapFormatError(
            "MO2 recovery pathsWritten includes an unrelated path"
        )


def _record_paths(
    job_id: str, receipt_id: str | None
) -> tuple[str, ...]:
    journal_path = (
        "runtime/jobs/mo2-bootstrap/"
        f"{job_id.removeprefix('bootstrap-job:')}/journal.json"
    )
    receipt_path = (
        None
        if receipt_id is None
        else (
            "games/skyrim-se-ae/tool-installations/mo2/"
            f"{receipt_id.removeprefix('bootstrap-receipt-sha256:')}.json"
        )
    )
    if receipt_path is None:
        return (journal_path,)
    return tuple(sorted((journal_path, receipt_path), key=str.casefold))


def _require_bootstrap_programs(
    programs: tuple[str, ...], extractor_path: str, *, include_extract: bool
) -> None:
    operations = ("version", "list-names", "list-types")
    if include_extract:
        operations = (*operations, "extract")
    expected = tuple(f"{extractor_path} [{operation}]" for operation in operations)
    if programs != expected:
        raise BootstrapFormatError(
            "MO2 setup program evidence does not match its bounded extractor calls"
        )


def _require_recovery_programs(result: RecoveryResult) -> None:
    if result.receipt is None:
        expected = tuple(
            rf"C:\Windows\System32\tar.exe [{operation}]"
            for operation in ("version", "list-names", "list-types")
        )
        if result.programs_launched not in {(), expected}:
            raise BootstrapFormatError(
                "MO2 recovery program evidence does not match the system extractor"
            )
        return
    expected = tuple(
        f"{result.receipt.extractor.executable.path} [{operation}]"
        for operation in ("version", "list-names", "list-types")
    )
    if result.programs_launched not in {(), expected}:
        raise BootstrapFormatError(
            "MO2 recovery program evidence does not match its bounded extractor calls"
        )


def _require_recovery_actions(result: RecoveryResult) -> None:
    if result.downloads or result.game_changes:
        raise BootstrapFormatError("MO2 recovery cannot report downloads or game changes")
    if result.receipt is None:
        if result.installations:
            raise BootstrapFormatError(
                "restore-only MO2 recovery cannot report installation actions"
            )
    elif result.receipt.mode is BootstrapReceiptMode.CREATED:
        if result.installations not in {(), ("portable-mo2-create",)}:
            raise BootstrapFormatError(
                "verified Create recovery has invalid installation actions"
            )
    elif result.installations or result.manager_changes:
        raise BootstrapFormatError(
            "verified Adopt recovery cannot report installation or manager changes"
        )


def _common_result(
    *,
    paths_written: Iterable[str],
    downloads: Iterable[str],
    installations: Iterable[str],
    manager_changes: Iterable[str],
    game_changes: Iterable[str],
    programs_launched: Iterable[str],
) -> dict[str, object]:
    paths = _relative_values(paths_written, "pathsWritten")
    download_values = _text_values(downloads, "downloadsPerformed")
    installation_values = _text_values(
        installations, "installationActionsPerformed"
    )
    manager_values = _relative_values(
        manager_changes, "managerChangesPerformed"
    )
    game_values = _relative_values(game_changes, "gameChangesPerformed")
    programs = _text_values(programs_launched, "programsLaunched", ordered=True)
    return {
        "schemaVersion": 1,
        "mo2Setup": None,
        "pathsWritten": list(paths),
        "downloadsPerformed": list(download_values),
        "installationActionsPerformed": list(installation_values),
        "managerChangesPerformed": list(manager_values),
        "gameChangesPerformed": list(game_values),
        "programsLaunched": list(programs),
    }


def _ordered_top_level(value: dict[str, object]) -> dict[str, object]:
    if set(value) != set(_TOP_LEVEL_KEYS):
        raise BootstrapFormatError("MO2 setup output has unexpected top-level fields")
    return {key: value[key] for key in _TOP_LEVEL_KEYS}


def _workspace_relative_path(path: Path, root: Path, label: str) -> str:
    candidate = Path(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise BootstrapFormatError(f"{label} escapes the workspace") from error
    return _relative_value(relative.as_posix(), label)


def _relative_values(values: Iterable[str], label: str) -> tuple[str, ...]:
    result = tuple(_relative_value(value, label) for value in values)
    _require_sorted_unique(result, label)
    return result


def _relative_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise BootstrapFormatError(f"{label} entries must be non-blank text")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows.is_absolute()
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BootstrapFormatError(f"{label} entries must be safe relative paths")
    return path.as_posix()


def _text_values(
    values: Iterable[str], label: str, *, ordered: bool = False
) -> tuple[str, ...]:
    result = tuple(values)
    if any(
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(ord(character) < 32 for character in value)
        for value in result
    ):
        raise BootstrapFormatError(f"{label} entries must be non-blank text")
    if len(set(result)) != len(result):
        raise BootstrapFormatError(f"{label} cannot contain duplicates")
    if not ordered:
        _require_sorted_unique(result, label)
    return result


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    identities = tuple(value.casefold() for value in values)
    if len(set(identities)) != len(identities):
        raise BootstrapFormatError(
            f"{label} cannot contain case-insensitive duplicates"
        )
    if values != tuple(sorted(values, key=lambda value: (value.casefold(), value))):
        raise BootstrapFormatError(f"{label} must be unique and sorted")


def _title(outcome: object) -> str:
    if not isinstance(outcome, str):
        raise BootstrapFormatError("MO2 setup outcome must be text")
    return _TITLES.get(outcome, outcome)


def _result_text(
    value: dict[str, object],
    heading: tuple[str, ...],
    *,
    actions_complete: bool = True,
) -> str:
    lines = list(heading)
    _append_values(lines, "Paths written", value["pathsWritten"])
    _append_values(
        lines,
        "Downloads performed",
        value["downloadsPerformed"],
        empty="No downloads were performed." if actions_complete else None,
    )
    _append_values(
        lines,
        "Installation actions performed",
        value["installationActionsPerformed"],
        empty="No installation actions were performed." if actions_complete else None,
    )
    _append_values(
        lines,
        "Manager changes performed",
        value["managerChangesPerformed"],
        empty="No manager changes were performed." if actions_complete else None,
    )
    _append_values(
        lines,
        "Game changes performed",
        value["gameChangesPerformed"],
        empty="No game changes were performed." if actions_complete else None,
    )
    _append_values(
        lines,
        "Programs launched",
        value["programsLaunched"],
        empty="No programs were launched." if actions_complete else None,
    )
    if not actions_complete:
        lines.append(
            "Action evidence is incomplete; empty action arrays do not claim no action."
        )
    lines.append("MO2 and Skyrim were not launched.")
    return "\n".join(lines) + "\n"


def _append_values(
    lines: list[str],
    label: str,
    values: object,
    *,
    empty: str | None = None,
) -> None:
    if not isinstance(values, list):
        raise BootstrapFormatError(f"{label} must be an array")
    if values:
        lines.append(f"{label}:")
        lines.extend(f"  - {value}" for value in values)
    elif empty is not None:
        lines.append(empty)


__all__ = [
    "apply_result_to_dict",
    "apply_result_to_text",
    "plan_result_to_dict",
    "plan_result_to_text",
    "recovery_result_to_dict",
    "recovery_result_to_text",
]
