"""Strict canonical JSON for immutable MO2 containment evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import PureWindowsPath
from typing import Any

from .mo2_containment_model import *


class ContainmentFormatError(ValueError):
    pass


WATCH_ROOT_KINDS = (
    "SourceMods",
    "LabProfile",
    "PlayProfile",
    "Downloads",
    "Overwrite",
    "BoundedGame",
    "ExternalLocalLow",
    "ExternalTempLow",
)

_SHA = re.compile(r"^[0-9a-f]{64}$")
_RUN = re.compile(r"^containment-run:[0-9a-f]{32}$")
_RESULT_ID = re.compile(r"^containment-result-sha256:[0-9a-f]{64}$")
_SOURCE_ARTIFACT_ID = re.compile(r"^containment-source-artifact-sha256:[0-9a-f]{64}$")
_GIT_ID = re.compile(r"^[0-9a-f]{40}$")
_WATCH_OUTCOME_ID = re.compile(r"^watch-outcome-sha256:[0-9a-f]{64}$")
_WATCH_REQUEST_ID = re.compile(r"^watch-request:[0-9a-f]{64}$")
_WATCH_SESSION_ID = re.compile(r"^watch-session:[0-9a-f]{64}$")
_JOURNAL_ID = re.compile(r"^containment-journal-sha256:[0-9a-f]{64}$")
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_RESULT = {
    "schemaVersion",
    "runId",
    "scenario",
    "outcome",
    "protectedBefore",
    "protectedAfter",
    "mo2Process",
    "sourceIntegrity",
    "stageIntegrity",
    "watchOutcomeId",
    "watchEvidenceCompletion",
    "scenarioStarted",
    "freshRetryEligible",
    "watcherEvents",
    "projectionCount",
    "projectionTargetsVerified",
    "projectionObservationComplete",
    "projectionPayloadBytesCopied",
    "productionBackupNames",
    "productionObservationComplete",
    "stagingNewNames",
    "stagingObservationComplete",
    "stagingOutputNames",
    "outputObservationComplete",
    "adoptedName",
    "adoptedTree",
    "adoptedIntegrity",
    "sourceRestoredAfterQuarantine",
    "reasons",
}
_WATCH_OUTCOME = {
    "schemaVersion",
    "requestId",
    "requestSha256",
    "sessionId",
    "runId",
    "scenario",
    "controllerPid",
    "controllerCreationTime",
    "workerPid",
    "workerCreationTime",
    "evidenceCompletion",
    "workerExitCode",
    "ready",
    "openedRootKinds",
    "events",
    "eventBytesSha256",
    "journalVolumeSerial",
    "journalFileId",
    "journalByteCount",
    "journalEventCount",
    "journalFinalSequence",
    "terminalBytesSha256",
    "rootIdentitiesUnchanged",
    "reasonCodes",
}
_RECOVERY = {
    "schemaVersion",
    "runId",
    "scenario",
    "journalId",
    "resultId",
    "cleanupStatus",
    "freshRunPermitted",
    "blockers",
}
_JOURNAL = {
    "schemaVersion",
    "runId",
    "scenario",
    "state",
    "sourceRoot",
    "stageRoot",
    "archivePath",
    "protectedModName",
    "expectedNewModName",
    "protectedBefore",
    "monitorPid",
    "mo2Pid",
    "error",
}
_DECISION = {
    "schemaVersion",
    "runId",
    "mechanism",
    "verdict",
    "scenarioResultIds",
    "reasons",
}
_DECISION_V2 = _DECISION | {"bindings"}
_BINDINGS = {
    "bindingVersion", "sourceCommitId", "sourceTreeId", "sourceArtifactId",
    "protocolVersion", "publicationPolicyVersion", "fixtureVersion",
    "effectReceiptVersion", "authorityPolicyVersion", "mo2Version",
    "mo2ExecutableSha256",
}
_TREE = {"sha256", "regularFileCount", "directoryCount", "totalSize"}
_PROTECTED = {
    "sourceMods",
    "labProfileSha256",
    "playProfileSha256",
    "downloads",
    "overwrite",
    "boundedGame",
}
_PROCESS = {
    "pid",
    "executable",
    "executableVersion",
    "arguments",
    "workingDirectory",
    "integrity",
}
_EVENT = {"sequence", "rootKind", "action", "relativePath"}
_MECHANISM = "isolated-low-integrity-junction-projection-v1"
_ADOPTION = {
    ContainmentScenario.NEW_FOLDER: (
        "ModLab Spike New",
        ("ModLab Spike New",),
        ("meshes/new-folder.bin", "meta.ini"),
    ),
    ContainmentScenario.FOMOD_DEPENDENCY: (
        "ModLab Spike FOMOD",
        ("ModLab Spike FOMOD",),
        ("always.txt", "dependency-seen.txt", "meta.ini"),
    ),
}
_REPLACEMENT = {
    ContainmentScenario.REPLACE_EXISTING: (
        "meshes/canary.bin",
        "meshes/new.bin",
        "meta.ini",
    ),
}


def scenario_journal_to_bytes(value: ScenarioJournal) -> bytes:
    return _bytes(scenario_journal_to_dict(value))


def scenario_journal_from_bytes(data: bytes) -> ScenarioJournal:
    return scenario_journal_from_dict(_decode(data, "scenario journal"))


def watch_outcome_to_bytes(value: WatchOutcome) -> bytes:
    return _bytes(watch_outcome_to_dict(value))


def watch_outcome_from_bytes(data: bytes) -> WatchOutcome:
    return watch_outcome_from_dict(_decode(data, "watch outcome"))


def watch_outcome_id_for(value: WatchOutcome) -> str:
    return "watch-outcome-sha256:" + hashlib.sha256(
        watch_outcome_to_bytes(value)
    ).hexdigest()


def scenario_recovery_to_bytes(value: ScenarioRecovery) -> bytes:
    return _bytes(scenario_recovery_to_dict(value))


def scenario_recovery_from_bytes(data: bytes) -> ScenarioRecovery:
    return scenario_recovery_from_dict(_decode(data, "scenario recovery"))


def scenario_result_to_bytes(
    value: ScenarioResult,
    watch_outcome: WatchOutcome,
) -> bytes:
    checked = scenario_result_from_dict(_result_dict(value), watch_outcome)
    return _bytes(_result_dict(checked))


def scenario_result_from_bytes(
    data: bytes,
    watch_outcome: WatchOutcome,
) -> ScenarioResult:
    return scenario_result_from_dict(
        _decode(data, "scenario result"),
        watch_outcome,
    )


def scenario_result_id_for(
    value: ScenarioResult,
    watch_outcome: WatchOutcome,
) -> str:
    return "containment-result-sha256:" + hashlib.sha256(
        scenario_result_to_bytes(value, watch_outcome)
    ).hexdigest()


def capability_decision_to_bytes(
    value: CapabilityDecision,
    scenario_results: tuple[ScenarioResult, ...] | None = None,
    watch_outcomes: tuple[WatchOutcome, ...] | None = None,
) -> bytes:
    return _bytes(
        capability_decision_to_dict(value, scenario_results, watch_outcomes)
    )


def capability_decision_from_bytes(
    data: bytes,
    scenario_results: tuple[ScenarioResult, ...] | None = None,
    watch_outcomes: tuple[WatchOutcome, ...] | None = None,
) -> CapabilityDecision:
    return capability_decision_from_dict(
        _decode(data, "capability decision"),
        scenario_results,
        watch_outcomes,
    )


def _capability_decision_probe_from_bytes(data: bytes) -> CapabilityDecision:
    return _capability_decision_from_dict(
        _decode(data, "capability decision"), None, None, probe=True
    )


def capability_decision_id_for(
    value: CapabilityDecision,
    scenario_results: tuple[ScenarioResult, ...] | None = None,
    watch_outcomes: tuple[WatchOutcome, ...] | None = None,
) -> str:
    return "containment-decision-sha256:" + hashlib.sha256(
        capability_decision_to_bytes(value, scenario_results, watch_outcomes)
    ).hexdigest()


def source_artifact_id_for(manifest: Mapping[str, Any]) -> str:
    """Return the content address of an exact canonical source-artifact manifest."""
    if type(manifest) is not dict:
        raise ContainmentFormatError("source artifact manifest must be an object")
    try:
        data = _bytes(manifest)
        if _decode(data, "source artifact manifest") != manifest:
            raise ContainmentFormatError("source artifact manifest is not canonical")
    except (TypeError, ValueError) as error:
        raise ContainmentFormatError("source artifact manifest is not canonical") from error
    return "containment-source-artifact-sha256:" + hashlib.sha256(data).hexdigest()


def scenario_journal_to_dict(value: ScenarioJournal) -> dict[str, Any]:
    return _journal_dict(scenario_journal_from_dict(_journal_dict(value)))


def scenario_journal_from_dict(value: Any) -> ScenarioJournal:
    data = _map(value, _JOURNAL, "scenario journal")
    state = _enum(ScenarioState, data["state"], "state")
    error = _opt_text(data["error"], "error")
    if state is ScenarioState.RECOVERY_REQUIRED and error is None:
        raise ContainmentFormatError("RecoveryRequired journal requires error")
    if state is not ScenarioState.RECOVERY_REQUIRED and error is not None:
        raise ContainmentFormatError(f"{state.value} journal cannot record error")

    monitor_pid = _opt_pos(data["monitorPid"], "monitorPid")
    mo2_pid = _opt_pos(data["mo2Pid"], "mo2Pid")
    if state in {ScenarioState.ARMED, ScenarioState.SCENARIO_STARTED}:
        if monitor_pid is None:
            raise ContainmentFormatError(
                f"{state.value} journal requires monitorPid"
            )
        if mo2_pid is not None:
            raise ContainmentFormatError(
                f"{state.value} journal cannot record mo2Pid"
            )
    if state in {ScenarioState.LAUNCHED, ScenarioState.CAPTURED}:
        if monitor_pid is None or mo2_pid is None:
            raise ContainmentFormatError(
                f"{state.value} journal requires monitorPid and mo2Pid"
            )

    return ScenarioJournal(
        _schema(data["schemaVersion"]),
        _run(data["runId"]),
        _enum(ContainmentScenario, data["scenario"], "scenario"),
        state,
        _abs(data["sourceRoot"], "sourceRoot"),
        _abs(data["stageRoot"], "stageRoot"),
        _abs(data["archivePath"], "archivePath"),
        _rel(data["protectedModName"], "protectedModName"),
        _rel(data["expectedNewModName"], "expectedNewModName"),
        _protected(data["protectedBefore"], "protectedBefore"),
        monitor_pid,
        mo2_pid,
        error,
    )


def watch_outcome_to_dict(value: WatchOutcome) -> dict[str, Any]:
    return _watch_outcome_dict(
        watch_outcome_from_dict(_watch_outcome_dict(value))
    )


def watch_outcome_from_dict(value: Any) -> WatchOutcome:
    data = _map(value, _WATCH_OUTCOME, "watch outcome")
    events = _events(data["events"], "events")
    outcome = WatchOutcome(
        schema_version=_schema(data["schemaVersion"]),
        request_id=_pattern(
            data["requestId"],
            _WATCH_REQUEST_ID,
            "requestId",
        ),
        request_sha256=_sha(data["requestSha256"], "requestSha256"),
        session_id=_pattern(
            data["sessionId"],
            _WATCH_SESSION_ID,
            "sessionId",
        ),
        run_id=_run(data["runId"]),
        scenario=_enum(ContainmentScenario, data["scenario"], "scenario"),
        controller_pid=_pos(data["controllerPid"], "controllerPid"),
        controller_creation_time=_pos(
            data["controllerCreationTime"],
            "controllerCreationTime",
        ),
        worker_pid=_pos(data["workerPid"], "workerPid"),
        worker_creation_time=_pos(
            data["workerCreationTime"],
            "workerCreationTime",
        ),
        evidence_completion=_enum(
            WatchEvidenceCompletion,
            data["evidenceCompletion"],
            "evidenceCompletion",
        ),
        worker_exit_code=_opt_nn(data["workerExitCode"], "workerExitCode"),
        ready=_bool(data["ready"], "ready"),
        opened_root_kinds=_root_kinds(data["openedRootKinds"]),
        events=events,
        event_bytes_sha256=_sha(
            data["eventBytesSha256"],
            "eventBytesSha256",
        ),
        journal_volume_serial=_nn(
            data["journalVolumeSerial"],
            "journalVolumeSerial",
        ),
        journal_file_id=_nn(data["journalFileId"], "journalFileId"),
        journal_byte_count=_nn(
            data["journalByteCount"],
            "journalByteCount",
        ),
        journal_event_count=_nn(
            data["journalEventCount"],
            "journalEventCount",
        ),
        journal_final_sequence=_nn(
            data["journalFinalSequence"],
            "journalFinalSequence",
        ),
        terminal_bytes_sha256=_sha(
            data["terminalBytesSha256"],
            "terminalBytesSha256",
        ),
        root_identities_unchanged=_bool(
            data["rootIdentitiesUnchanged"],
            "rootIdentitiesUnchanged",
        ),
        reason_codes=_sorted_text(data["reasonCodes"], "reasonCodes"),
    )
    _check_watch_outcome(outcome)
    return outcome


def scenario_recovery_to_dict(value: ScenarioRecovery) -> dict[str, Any]:
    return _recovery_dict(
        scenario_recovery_from_dict(_recovery_dict(value))
    )


def scenario_recovery_from_dict(value: Any) -> ScenarioRecovery:
    data = _map(value, _RECOVERY, "scenario recovery")
    result_id = _opt_pattern(data["resultId"], _RESULT_ID, "resultId")
    recovery = ScenarioRecovery(
        schema_version=_schema(data["schemaVersion"]),
        run_id=_run(data["runId"]),
        scenario=_enum(ContainmentScenario, data["scenario"], "scenario"),
        journal_id=_pattern(data["journalId"], _JOURNAL_ID, "journalId"),
        result_id=result_id,
        cleanup_status=_enum(
            ScenarioCleanupStatus,
            data["cleanupStatus"],
            "cleanupStatus",
        ),
        fresh_run_permitted=_bool(
            data["freshRunPermitted"],
            "freshRunPermitted",
        ),
        blockers=_sorted_text(data["blockers"], "blockers"),
    )
    if recovery.cleanup_status is ScenarioCleanupStatus.REFUSED:
        if not recovery.blockers:
            raise ContainmentFormatError("Refused cleanup requires blockers")
        if recovery.fresh_run_permitted:
            raise ContainmentFormatError(
                "Refused cleanup cannot permit a fresh run"
            )
    elif recovery.blockers:
        raise ContainmentFormatError("Succeeded cleanup cannot record blockers")
    if recovery.fresh_run_permitted:
        if recovery.cleanup_status is not ScenarioCleanupStatus.SUCCEEDED:
            raise ContainmentFormatError(
                "fresh run permission requires succeeded cleanup"
            )
        if recovery.result_id is None:
            raise ContainmentFormatError(
                "fresh run permission requires immutable incomplete resultId"
            )
    return recovery


def scenario_result_to_dict(
    value: ScenarioResult,
    watch_outcome: WatchOutcome,
) -> dict[str, Any]:
    checked = scenario_result_from_dict(_result_dict(value), watch_outcome)
    return _result_dict(checked)


def scenario_result_from_dict(
    value: Any,
    watch_outcome: WatchOutcome,
) -> ScenarioResult:
    bound_outcome = watch_outcome_from_dict(_watch_outcome_dict(watch_outcome))
    data = _map(value, _RESULT, "scenario result")
    result = ScenarioResult(
        schema_version=_schema(data["schemaVersion"]),
        run_id=_run(data["runId"]),
        scenario=_enum(ContainmentScenario, data["scenario"], "scenario"),
        outcome=_enum(ScenarioOutcome, data["outcome"], "outcome"),
        protected_before=_protected(data["protectedBefore"], "protectedBefore"),
        protected_after=_protected(data["protectedAfter"], "protectedAfter"),
        mo2_process=(
            None
            if data["mo2Process"] is None
            else _process(data["mo2Process"], "mo2Process")
        ),
        source_integrity=_enum(
            IntegrityObservation,
            data["sourceIntegrity"],
            "sourceIntegrity",
        ),
        stage_integrity=_enum(
            IntegrityObservation,
            data["stageIntegrity"],
            "stageIntegrity",
        ),
        watch_outcome_id=_pattern(
            data["watchOutcomeId"],
            _WATCH_OUTCOME_ID,
            "watchOutcomeId",
        ),
        watch_evidence_completion=_enum(
            WatchEvidenceCompletion,
            data["watchEvidenceCompletion"],
            "watchEvidenceCompletion",
        ),
        scenario_started=_bool(data["scenarioStarted"], "scenarioStarted"),
        fresh_retry_eligible=_bool(
            data["freshRetryEligible"],
            "freshRetryEligible",
        ),
        watcher_events=_events(data["watcherEvents"], "watcherEvents"),
        projection_count=_nn(data["projectionCount"], "projectionCount"),
        projection_targets_verified=_bool(
            data["projectionTargetsVerified"],
            "projectionTargetsVerified",
        ),
        projection_observation_complete=_bool(
            data["projectionObservationComplete"],
            "projectionObservationComplete",
        ),
        projection_payload_bytes_copied=_nn(
            data["projectionPayloadBytesCopied"],
            "projectionPayloadBytesCopied",
        ),
        production_backup_names=_sorted_rel(
            data["productionBackupNames"],
            "productionBackupNames",
        ),
        production_observation_complete=_bool(
            data["productionObservationComplete"],
            "productionObservationComplete",
        ),
        staging_new_names=_sorted_rel(
            data["stagingNewNames"],
            "stagingNewNames",
        ),
        staging_observation_complete=_bool(
            data["stagingObservationComplete"],
            "stagingObservationComplete",
        ),
        staging_output_names=_sorted_rel(
            data["stagingOutputNames"],
            "stagingOutputNames",
        ),
        output_observation_complete=_bool(
            data["outputObservationComplete"],
            "outputObservationComplete",
        ),
        adopted_name=(
            None
            if data["adoptedName"] is None
            else _rel(data["adoptedName"], "adoptedName")
        ),
        adopted_tree=(
            None
            if data["adoptedTree"] is None
            else _tree(data["adoptedTree"], "adoptedTree")
        ),
        adopted_integrity=(
            None
            if data["adoptedIntegrity"] is None
            else _enum(
                IntegrityObservation,
                data["adoptedIntegrity"],
                "adoptedIntegrity",
            )
        ),
        source_restored_after_quarantine=_bool(
            data["sourceRestoredAfterQuarantine"],
            "sourceRestoredAfterQuarantine",
        ),
        reasons=_sorted_text(data["reasons"], "reasons"),
    )
    _check_result_binding(result, bound_outcome)
    _check_result(result)
    return result


def capability_decision_to_dict(
    value: CapabilityDecision,
    scenario_results: tuple[ScenarioResult, ...] | None = None,
    watch_outcomes: tuple[WatchOutcome, ...] | None = None,
) -> dict[str, Any]:
    return _decision_dict(
        capability_decision_from_dict(
            _decision_dict(value),
            scenario_results,
            watch_outcomes,
        )
    )


def capability_decision_from_dict(
    value: Any,
    scenario_results: tuple[ScenarioResult, ...] | None = None,
    watch_outcomes: tuple[WatchOutcome, ...] | None = None,
) -> CapabilityDecision:
    return _capability_decision_from_dict(
        value, scenario_results, watch_outcomes, probe=False
    )


def _capability_decision_from_dict(
    value: Any,
    scenario_results: tuple[ScenarioResult, ...] | None,
    watch_outcomes: tuple[WatchOutcome, ...] | None,
    *,
    probe: bool,
) -> CapabilityDecision:
    if type(value) is not dict:
        raise ContainmentFormatError("capability decision must be an object")
    is_v2 = set(value) == _DECISION_V2
    data = _map(value, _DECISION_V2 if is_v2 else _DECISION, "capability decision")
    if is_v2:
        identifiers = _decision_ids(data["scenarioResultIds"])
        bindings = _bindings(data["bindings"])
    else:
        identifiers = _ids(data["scenarioResultIds"])
        bindings = None
    decision = CapabilityDecision(
        schema_version=_decision_schema(data["schemaVersion"], is_v2),
        run_id=_run(data["runId"]),
        mechanism=_fixed(data["mechanism"], _MECHANISM, "mechanism"),
        verdict=_enum(CapabilityVerdict, data["verdict"], "verdict"),
        scenario_result_ids=identifiers,
        reasons=_sorted_text(data["reasons"], "reasons"),
        bindings=bindings,
    )
    if decision.verdict is CapabilityVerdict.SUPPORTED:
        if len(identifiers) != len(ContainmentScenario):
            raise ContainmentFormatError(
                "Supported decision requires four passing scenarios"
            )
        if decision.reasons:
            raise ContainmentFormatError(
                "Supported decision cannot record reasons"
            )
    elif not decision.reasons:
        raise ContainmentFormatError(
            f"{decision.verdict.value} decision requires reasons"
        )
    _check_decision_results(decision, scenario_results, watch_outcomes, probe=probe)
    return decision


def _check_watch_outcome(outcome: WatchOutcome) -> None:
    event_bytes = b"".join(_bytes(_event_dict(event)) for event in outcome.events)
    if outcome.event_bytes_sha256 != hashlib.sha256(event_bytes).hexdigest():
        raise ContainmentFormatError("watch outcome event hash mismatch")
    if outcome.journal_byte_count != len(event_bytes):
        raise ContainmentFormatError("watch outcome journal byte count mismatch")
    if outcome.journal_event_count != len(outcome.events):
        raise ContainmentFormatError("watch outcome journal event count mismatch")
    final_sequence = outcome.events[-1].sequence if outcome.events else 0
    if outcome.journal_final_sequence != final_sequence:
        raise ContainmentFormatError("watch outcome journal final sequence mismatch")

    if outcome.evidence_completion is WatchEvidenceCompletion.COMPLETED:
        if outcome.worker_exit_code != 0:
            raise ContainmentFormatError(
                "Completed watch outcome requires worker exit code 0"
            )
        if not outcome.ready:
            raise ContainmentFormatError("Completed watch outcome requires ready")
        if outcome.opened_root_kinds != WATCH_ROOT_KINDS:
            raise ContainmentFormatError(
                "Completed watch outcome requires all eight root kinds"
            )
        if not outcome.root_identities_unchanged:
            raise ContainmentFormatError(
                "Completed watch outcome requires unchanged root identities"
            )
        if outcome.journal_volume_serial <= 0 or outcome.journal_file_id <= 0:
            raise ContainmentFormatError(
                "Completed watch outcome requires journal identity"
            )
        if outcome.reason_codes:
            raise ContainmentFormatError(
                "Completed watch outcome cannot record reason codes"
            )
    elif not outcome.reason_codes:
        raise ContainmentFormatError(
            "Incomplete watch outcome requires reason codes"
        )


def _check_result_binding(
    result: ScenarioResult,
    watch_outcome: WatchOutcome,
) -> None:
    if result.watch_outcome_id != watch_outcome_id_for(watch_outcome):
        raise ContainmentFormatError("scenario result watch outcome ID mismatch")
    if result.run_id != watch_outcome.run_id:
        raise ContainmentFormatError("scenario result/watch outcome runId mismatch")
    if result.scenario is not watch_outcome.scenario:
        raise ContainmentFormatError("scenario result/watch outcome scenario mismatch")
    if result.watch_evidence_completion is not watch_outcome.evidence_completion:
        raise ContainmentFormatError(
            "scenario result/watch outcome evidence completion mismatch"
        )
    if result.watcher_events != watch_outcome.events:
        raise ContainmentFormatError("scenario result/watch outcome events mismatch")


def _check_result(result: ScenarioResult) -> None:
    adoption = (
        result.adopted_name,
        result.adopted_tree,
        result.adopted_integrity,
    )
    if any(item is None for item in adoption) and any(
        item is not None for item in adoption
    ):
        raise ContainmentFormatError(
            "adoption evidence must be wholly null or present"
        )

    if result.outcome is ScenarioOutcome.PASSED:
        _check_passed_result(result, adoption)
    elif result.outcome is ScenarioOutcome.FAILED:
        if not result.reasons:
            raise ContainmentFormatError("Failed result requires reasons")
        if not result.scenario_started:
            raise ContainmentFormatError("Failed result requires ScenarioStarted")
        if result.fresh_retry_eligible:
            raise ContainmentFormatError("Failed result cannot be retry eligible")
        if (
            not result.watcher_events
            and result.protected_before == result.protected_after
            and not (
                result.watch_evidence_completion is WatchEvidenceCompletion.COMPLETED
                and _failed_policy_violations(result)
            )
        ):
            raise ContainmentFormatError(
                "Failed result requires positive breach evidence"
            )
    else:
        if not result.reasons:
            raise ContainmentFormatError("Incomplete result requires reasons")
        if result.fresh_retry_eligible:
            if result.scenario_started:
                raise ContainmentFormatError(
                    "fresh retry eligibility requires pre-scenario interruption"
                )
            if (
                result.watch_evidence_completion
                is not WatchEvidenceCompletion.INCOMPLETE
            ):
                raise ContainmentFormatError(
                    "fresh retry eligibility requires Incomplete watcher evidence"
                )


def deterministic_policy_violations(result: ScenarioResult) -> tuple[str, ...]:
    """Recompute typed, reason-independent deterministic scenario violations."""
    violations: set[str] = set()
    process = result.mo2_process
    if process is not None:
        expected_executable = PureWindowsPath(process.working_directory) / "ModOrganizer.exe"
        if (
            process.executable_version != "2.5.2.0"
            or process.arguments != ("--profile", "ModLab - Lab")
            or PureWindowsPath(process.executable) != expected_executable
            or (
                process.integrity is not IntegrityObservation.UNKNOWN
                and process.integrity is not IntegrityObservation.LOW
            )
        ):
            violations.add("mo2-process-evidence-invalid")
    if result.source_integrity not in {
        IntegrityObservation.UNKNOWN,
        IntegrityObservation.MEDIUM,
        IntegrityObservation.HIGH,
        IntegrityObservation.SYSTEM,
    }:
        violations.add("source-integrity-invalid")
    if result.stage_integrity not in {
        IntegrityObservation.UNKNOWN,
        IntegrityObservation.LOW,
    }:
        violations.add("stage-integrity-invalid")
    if result.projection_observation_complete:
        if result.projection_count <= 0:
            violations.add("projection-count-invalid")
        if not result.projection_targets_verified:
            violations.add("projection-target-changed")
    if result.projection_payload_bytes_copied != 0:
        violations.add("projection-payload-copied")
    if result.production_observation_complete and result.production_backup_names:
        violations.add("production-backup-created")
    if not result.source_restored_after_quarantine:
        violations.add("source-not-restored-after-quarantine")

    adoption = (result.adopted_name, result.adopted_tree, result.adopted_integrity)
    if result.scenario in _ADOPTION:
        name, staging_new, staging_output = _ADOPTION[result.scenario]
        if (
            result.staging_observation_complete
            and result.staging_new_names != staging_new
            and (result.staging_new_names or result.output_observation_complete)
        ):
            violations.add("staging-new-folder-set-invalid")
        if (
            result.output_observation_complete
            and result.staging_output_names != staging_output
        ):
            violations.add("staging-output-set-invalid")
        if result.output_observation_complete and any(
            item is not None for item in adoption
        ) and (
            result.adopted_name != name
            or result.adopted_tree is None
            or result.adopted_integrity
            not in {IntegrityObservation.UNKNOWN, IntegrityObservation.MEDIUM}
        ):
            violations.add("adoption-proof-invalid")
    elif result.scenario in _REPLACEMENT:
        if (
            result.output_observation_complete
            and result.staging_output_names != _REPLACEMENT[result.scenario]
        ):
            violations.add("staging-output-set-invalid")
        if (
            (result.staging_observation_complete and result.staging_new_names)
            or any(item is not None for item in adoption)
        ):
            violations.add("unexpected-staging-output")
    elif (
        (result.staging_observation_complete and result.staging_new_names)
        or (result.output_observation_complete and result.staging_output_names)
        or any(item is not None for item in adoption)
    ):
        violations.add("unexpected-staging-output")
    return tuple(sorted(violations))


def _failed_policy_violations(result: ScenarioResult) -> tuple[str, ...]:
    """Accept over-conservative immutable pre-v3 failures without producing them."""
    violations = set(deterministic_policy_violations(result))
    if result.scenario in _ADOPTION:
        _name, staging_new, staging_output = _ADOPTION[result.scenario]
        if (
            result.staging_observation_complete
            and result.staging_new_names != staging_new
        ):
            violations.add("staging-new-folder-set-invalid")
        if (
            result.output_observation_complete
            and result.staging_output_names == staging_output
            and "staging-output-set-invalid" in result.reasons
        ):
            violations.add("staging-output-set-invalid")
    return tuple(sorted(violations))


def _check_passed_result(
    result: ScenarioResult,
    adoption: tuple[str | None, TreeIdentity | None, IntegrityObservation | None],
) -> None:
    if result.protected_before != result.protected_after:
        raise ContainmentFormatError(
            "Passed result requires protectedAfter identical to protectedBefore"
        )
    if (
        result.watch_evidence_completion
        is not WatchEvidenceCompletion.COMPLETED
    ):
        raise ContainmentFormatError(
            "Passed result requires Completed watcher evidence"
        )
    if not result.scenario_started:
        raise ContainmentFormatError("Passed result requires ScenarioStarted")
    if result.fresh_retry_eligible:
        raise ContainmentFormatError("Passed result cannot be retry eligible")
    if result.watcher_events:
        raise ContainmentFormatError("Passed result cannot record watcher events")
    if result.mo2_process is None:
        raise ContainmentFormatError("Passed result requires MO2 process evidence")
    if result.mo2_process.executable_version != "2.5.2.0":
        raise ContainmentFormatError("Passed result requires MO2 version 2.5.2.0")
    if result.mo2_process.integrity is not IntegrityObservation.LOW:
        raise ContainmentFormatError("Passed result requires Low MO2 process")
    if result.stage_integrity is not IntegrityObservation.LOW:
        raise ContainmentFormatError("Passed result requires Low stage integrity")
    if result.source_integrity not in {
        IntegrityObservation.MEDIUM,
        IntegrityObservation.HIGH,
        IntegrityObservation.SYSTEM,
    }:
        raise ContainmentFormatError(
            "Passed result requires Medium-or-higher source integrity"
        )
    if (
        not result.projection_observation_complete
        or result.projection_count <= 0
        or not result.projection_targets_verified
    ):
        raise ContainmentFormatError("Passed result requires verified projections")
    if result.projection_payload_bytes_copied:
        raise ContainmentFormatError(
            "Passed result requires zero copied projection payload bytes"
        )
    if (
        not result.production_observation_complete
        or result.production_backup_names
    ):
        raise ContainmentFormatError(
            "Passed result cannot record production backups"
        )
    if not result.source_restored_after_quarantine:
        raise ContainmentFormatError(
            "Passed result requires restored source root"
        )
    if result.reasons:
        raise ContainmentFormatError("Passed result cannot record reasons")

    if result.scenario in _ADOPTION:
        if (
            not result.staging_observation_complete
            or not result.output_observation_complete
        ):
            raise ContainmentFormatError(
                "Passed adoption result requires complete staging/output observations"
            )
        name, staging_new, staging_output = _ADOPTION[result.scenario]
        if (
            result.adopted_name,
            result.staging_new_names,
            result.staging_output_names,
        ) != (name, staging_new, staging_output):
            raise ContainmentFormatError(
                f"Passed {result.scenario.value} result requires exact adopted staging outputs"
            )
        if (
            result.adopted_tree is None
            or result.adopted_integrity is not IntegrityObservation.MEDIUM
        ):
            raise ContainmentFormatError(
                f"Passed {result.scenario.value} result requires Medium adopted tree evidence"
            )
    elif result.scenario in _REPLACEMENT:
        if (
            not result.staging_observation_complete
            or not result.output_observation_complete
            or any(item is not None for item in adoption)
            or result.staging_new_names
            or result.staging_output_names != _REPLACEMENT[result.scenario]
        ):
            raise ContainmentFormatError(
                "Passed ReplaceExisting result requires exact quarantined replacement output"
            )
    elif (
        not result.staging_observation_complete
        or not result.output_observation_complete
        or any(item is not None for item in adoption)
        or result.staging_new_names
        or result.staging_output_names
    ):
        raise ContainmentFormatError(
            f"Passed {result.scenario.value} result cannot record adopted output"
        )


def _check_decision_results(
    decision: CapabilityDecision,
    scenario_results: tuple[ScenarioResult, ...] | None,
    watch_outcomes: tuple[WatchOutcome, ...] | None,
    *,
    probe: bool = False,
) -> None:
    if scenario_results is None and watch_outcomes is None:
        if decision.bindings is not None and probe:
            return
        if decision.scenario_result_ids:
            raise ContainmentFormatError(
                "decision result IDs require corresponding result/outcome pairs"
            )
        if decision.verdict is CapabilityVerdict.SUPPORTED:
            raise ContainmentFormatError(
                "Supported decision requires corresponding scenario results and watch outcomes"
            )
        return
    if scenario_results is None or watch_outcomes is None:
        raise ContainmentFormatError(
            "decision evidence requires both scenario results and watch outcomes"
        )
    results = tuple(scenario_results)
    outcomes = tuple(watch_outcomes)
    if (
        len(results) != len(outcomes)
        or len(results) != len(decision.scenario_result_ids)
    ):
        raise ContainmentFormatError(
            "decision requires equal result ID, scenario result, and watch outcome counts"
        )
    if not results:
        if decision.verdict is CapabilityVerdict.SUPPORTED:
            raise ContainmentFormatError(
                "Supported decision requires four corresponding result/outcome pairs"
            )
        return

    result_scenarios = tuple(result.scenario for result in results)
    expected_scenarios = tuple(
        scenario
        for scenario in ContainmentScenario
        if scenario in set(result_scenarios)
    )
    if result_scenarios != expected_scenarios:
        raise ContainmentFormatError(
            "decision scenario results must use enum order without duplicates"
        )
    if tuple(outcome.scenario for outcome in outcomes) != result_scenarios:
        raise ContainmentFormatError(
            "decision watch outcomes must match result scenarios in enum order"
        )
    if any(result.run_id != decision.run_id for result in results):
        raise ContainmentFormatError(
            "decision scenario results must share runId"
        )
    if any(outcome.run_id != decision.run_id for outcome in outcomes):
        raise ContainmentFormatError(
            "decision watch outcomes must share runId"
        )

    checked_outcomes = tuple(
        watch_outcome_from_dict(_watch_outcome_dict(outcome))
        for outcome in outcomes
    )
    checked_results = tuple(
        scenario_result_from_dict(_result_dict(result), outcome)
        for result, outcome in zip(results, checked_outcomes, strict=True)
    )
    identifiers = tuple(
        scenario_result_id_for(result, outcome)
        for result, outcome in zip(checked_results, checked_outcomes, strict=True)
    )
    if identifiers != decision.scenario_result_ids:
        raise ContainmentFormatError(
            "decision scenario result IDs do not match evidence"
        )

    has_failed = any(
        result.outcome is ScenarioOutcome.FAILED
        for result in checked_results
    )
    if has_failed and decision.verdict is not CapabilityVerdict.REJECTED:
        raise ContainmentFormatError(
            "Failed scenario result requires Rejected capability verdict"
        )
    if decision.verdict is CapabilityVerdict.SUPPORTED:
        if len(checked_results) != len(ContainmentScenario):
            raise ContainmentFormatError(
                "Supported decision requires four corresponding result/outcome pairs"
            )
        if result_scenarios != tuple(ContainmentScenario):
            raise ContainmentFormatError(
                "Supported decision requires all scenarios in enum order"
            )
        if any(
            result.outcome is not ScenarioOutcome.PASSED
            for result in checked_results
        ):
            raise ContainmentFormatError(
                "Supported decision requires Passed scenario results"
            )


def _journal_dict(value: ScenarioJournal) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "state": value.state.value,
        "sourceRoot": value.source_root,
        "stageRoot": value.stage_root,
        "archivePath": value.archive_path,
        "protectedModName": value.protected_mod_name,
        "expectedNewModName": value.expected_new_mod_name,
        "protectedBefore": _protected_dict(value.protected_before),
        "monitorPid": value.monitor_pid,
        "mo2Pid": value.mo2_pid,
        "error": value.error,
    }


def _watch_outcome_dict(value: WatchOutcome) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "requestId": value.request_id,
        "requestSha256": value.request_sha256,
        "sessionId": value.session_id,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "controllerPid": value.controller_pid,
        "controllerCreationTime": value.controller_creation_time,
        "workerPid": value.worker_pid,
        "workerCreationTime": value.worker_creation_time,
        "evidenceCompletion": value.evidence_completion.value,
        "workerExitCode": value.worker_exit_code,
        "ready": value.ready,
        "openedRootKinds": list(value.opened_root_kinds),
        "events": [_event_dict(event) for event in value.events],
        "eventBytesSha256": value.event_bytes_sha256,
        "journalVolumeSerial": value.journal_volume_serial,
        "journalFileId": value.journal_file_id,
        "journalByteCount": value.journal_byte_count,
        "journalEventCount": value.journal_event_count,
        "journalFinalSequence": value.journal_final_sequence,
        "terminalBytesSha256": value.terminal_bytes_sha256,
        "rootIdentitiesUnchanged": value.root_identities_unchanged,
        "reasonCodes": list(value.reason_codes),
    }


def _recovery_dict(value: ScenarioRecovery) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "journalId": value.journal_id,
        "resultId": value.result_id,
        "cleanupStatus": value.cleanup_status.value,
        "freshRunPermitted": value.fresh_run_permitted,
        "blockers": list(value.blockers),
    }


def _result_dict(value: ScenarioResult) -> dict[str, Any]:
    return {
        "schemaVersion": value.schema_version,
        "runId": value.run_id,
        "scenario": value.scenario.value,
        "outcome": value.outcome.value,
        "protectedBefore": _protected_dict(value.protected_before),
        "protectedAfter": _protected_dict(value.protected_after),
        "mo2Process": (
            None if value.mo2_process is None else _process_dict(value.mo2_process)
        ),
        "sourceIntegrity": value.source_integrity.value,
        "stageIntegrity": value.stage_integrity.value,
        "watchOutcomeId": value.watch_outcome_id,
        "watchEvidenceCompletion": value.watch_evidence_completion.value,
        "scenarioStarted": value.scenario_started,
        "freshRetryEligible": value.fresh_retry_eligible,
        "watcherEvents": [_event_dict(event) for event in value.watcher_events],
        "projectionCount": value.projection_count,
        "projectionTargetsVerified": value.projection_targets_verified,
        "projectionObservationComplete": value.projection_observation_complete,
        "projectionPayloadBytesCopied": value.projection_payload_bytes_copied,
        "productionBackupNames": list(value.production_backup_names),
        "productionObservationComplete": value.production_observation_complete,
        "stagingNewNames": list(value.staging_new_names),
        "stagingObservationComplete": value.staging_observation_complete,
        "stagingOutputNames": list(value.staging_output_names),
        "outputObservationComplete": value.output_observation_complete,
        "adoptedName": value.adopted_name,
        "adoptedTree": (
            None if value.adopted_tree is None else _tree_dict(value.adopted_tree)
        ),
        "adoptedIntegrity": (
            None
            if value.adopted_integrity is None
            else value.adopted_integrity.value
        ),
        "sourceRestoredAfterQuarantine": value.source_restored_after_quarantine,
        "reasons": list(value.reasons),
    }


def _decision_dict(value: CapabilityDecision) -> dict[str, Any]:
    if value.bindings is not None:
        identifiers = _bound_decision_ids(value.scenario_result_ids)
        return {
            "schemaVersion": value.schema_version,
            "runId": value.run_id,
            "mechanism": value.mechanism,
            "verdict": value.verdict.value,
            "scenarioResultIds": {
                scenario.value: identifier
                for scenario, identifier in zip(ContainmentScenario, identifiers, strict=True)
            },
            "reasons": list(value.reasons),
            "bindings": _bindings_dict(value.bindings),
        }
    return {
        "schemaVersion": value.schema_version,
        "runId": value.run_id,
        "mechanism": value.mechanism,
        "verdict": value.verdict.value,
        "scenarioResultIds": list(value.scenario_result_ids),
        "reasons": list(value.reasons),
    }


def _bindings(value: Any) -> DecisionBindings:
    data = _map(value, _BINDINGS, "decision bindings")
    return DecisionBindings(
        binding_version=_fixed_int(data["bindingVersion"], 2, "bindingVersion"),
        source_commit_id=_pattern(data["sourceCommitId"], _GIT_ID, "sourceCommitId"),
        source_tree_id=_pattern(data["sourceTreeId"], _GIT_ID, "sourceTreeId"),
        source_artifact_id=_pattern(data["sourceArtifactId"], _SOURCE_ARTIFACT_ID, "sourceArtifactId"),
        protocol_version=_fixed_int(data["protocolVersion"], 2, "protocolVersion"),
        publication_policy_version=_fixed(
            data["publicationPolicyVersion"], "handle-pinned-no-replace-v2", "publicationPolicyVersion"
        ),
        fixture_version=_fixed_int(data["fixtureVersion"], 2, "fixtureVersion"),
        effect_receipt_version=_fixed_int(data["effectReceiptVersion"], 1, "effectReceiptVersion"),
        authority_policy_version=_fixed_int(data["authorityPolicyVersion"], 1, "authorityPolicyVersion"),
        mo2_version=_fixed(data["mo2Version"], "2.5.2.0", "mo2Version"),
        mo2_executable_sha256=_sha(data["mo2ExecutableSha256"], "mo2ExecutableSha256"),
    )


def _bindings_dict(value: DecisionBindings) -> dict[str, Any]:
    checked = _bindings({
        "bindingVersion": value.binding_version,
        "sourceCommitId": value.source_commit_id,
        "sourceTreeId": value.source_tree_id,
        "sourceArtifactId": value.source_artifact_id,
        "protocolVersion": value.protocol_version,
        "publicationPolicyVersion": value.publication_policy_version,
        "fixtureVersion": value.fixture_version,
        "effectReceiptVersion": value.effect_receipt_version,
        "authorityPolicyVersion": value.authority_policy_version,
        "mo2Version": value.mo2_version,
        "mo2ExecutableSha256": value.mo2_executable_sha256,
    })
    return {
        "bindingVersion": checked.binding_version,
        "sourceCommitId": checked.source_commit_id,
        "sourceTreeId": checked.source_tree_id,
        "sourceArtifactId": checked.source_artifact_id,
        "protocolVersion": checked.protocol_version,
        "publicationPolicyVersion": checked.publication_policy_version,
        "fixtureVersion": checked.fixture_version,
        "effectReceiptVersion": checked.effect_receipt_version,
        "authorityPolicyVersion": checked.authority_policy_version,
        "mo2Version": checked.mo2_version,
        "mo2ExecutableSha256": checked.mo2_executable_sha256,
    }


def _decision_ids(value: Any) -> tuple[str, ...]:
    if type(value) is not dict or set(value) != {scenario.value for scenario in ContainmentScenario}:
        raise ContainmentFormatError("decision scenario result IDs must be a complete scenario mapping")
    return _bound_decision_ids(tuple(
        _pattern(value[scenario.value], _RESULT_ID, f"scenarioResultIds.{scenario.value}")
        for scenario in ContainmentScenario
    ))


def _bound_decision_ids(value: Any) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) != len(ContainmentScenario):
        raise ContainmentFormatError("bound decision requires exactly four scenario result IDs")
    identifiers = tuple(
        _pattern(item, _RESULT_ID, "scenarioResultIds") for item in value
    )
    if len(set(identifiers)) != len(identifiers):
        raise ContainmentFormatError("bound decision scenario result IDs must be unique")
    return identifiers


def _tree_dict(value: TreeIdentity) -> dict[str, Any]:
    return {
        "sha256": value.sha256,
        "regularFileCount": value.regular_file_count,
        "directoryCount": value.directory_count,
        "totalSize": value.total_size,
    }


def _tree(value: Any, label: str) -> TreeIdentity:
    data = _map(value, _TREE, label)
    return TreeIdentity(
        _sha(data["sha256"], label + ".sha256"),
        _nn(data["regularFileCount"], label + ".regularFileCount"),
        _nn(data["directoryCount"], label + ".directoryCount"),
        _nn(data["totalSize"], label + ".totalSize"),
    )


def _protected_dict(value: ProtectedState) -> dict[str, Any]:
    return {
        "sourceMods": _tree_dict(value.source_mods),
        "labProfileSha256": value.lab_profile_sha256,
        "playProfileSha256": value.play_profile_sha256,
        "downloads": _tree_dict(value.downloads),
        "overwrite": _tree_dict(value.overwrite),
        "boundedGame": _tree_dict(value.bounded_game),
    }


def _protected(value: Any, label: str) -> ProtectedState:
    data = _map(value, _PROTECTED, label)
    return ProtectedState(
        _tree(data["sourceMods"], label + ".sourceMods"),
        _sha(data["labProfileSha256"], label + ".labProfileSha256"),
        _sha(data["playProfileSha256"], label + ".playProfileSha256"),
        _tree(data["downloads"], label + ".downloads"),
        _tree(data["overwrite"], label + ".overwrite"),
        _tree(data["boundedGame"], label + ".boundedGame"),
    )


def _process_dict(value: ProcessEvidence) -> dict[str, Any]:
    return {
        "pid": value.pid,
        "executable": value.executable,
        "executableVersion": value.executable_version,
        "arguments": list(value.arguments),
        "workingDirectory": value.working_directory,
        "integrity": value.integrity.value,
    }


def _process(value: Any, label: str) -> ProcessEvidence:
    data = _map(value, _PROCESS, label)
    return ProcessEvidence(
        _pos(data["pid"], label + ".pid"),
        _abs(data["executable"], label + ".executable"),
        _text(data["executableVersion"], label + ".executableVersion"),
        _texts(data["arguments"], label + ".arguments"),
        _abs(data["workingDirectory"], label + ".workingDirectory"),
        _enum(IntegrityObservation, data["integrity"], label + ".integrity"),
    )


def _event_dict(value: WatcherEvent) -> dict[str, Any]:
    return {
        "sequence": value.sequence,
        "rootKind": value.root_kind,
        "action": value.action,
        "relativePath": value.relative_path,
    }


def _events(value: Any, label: str) -> tuple[WatcherEvent, ...]:
    if not isinstance(value, list):
        raise ContainmentFormatError(f"{label} must be an array")
    events = []
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        data = _map(item, _EVENT, item_label)
        root_kind = _token(data["rootKind"], item_label + ".rootKind")
        if root_kind not in WATCH_ROOT_KINDS:
            raise ContainmentFormatError(
                f"{item_label}.rootKind has an unknown root kind"
            )
        events.append(
            WatcherEvent(
                _pos(data["sequence"], item_label + ".sequence"),
                root_kind,
                _token(data["action"], item_label + ".action"),
                _rel(data["relativePath"], item_label + ".relativePath"),
            )
        )
    sequence = tuple(event.sequence for event in events)
    expected = tuple(range(1, len(events) + 1))
    if sequence != expected:
        raise ContainmentFormatError(
            f"{label} must use contiguous sequence order starting at 1"
        )
    return tuple(events)


def _root_kinds(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContainmentFormatError("openedRootKinds must be an array")
    kinds = tuple(
        _token(item, f"openedRootKinds[{index}]")
        for index, item in enumerate(value)
    )
    if len(kinds) != len(set(kinds)):
        raise ContainmentFormatError("openedRootKinds contains duplicates")
    if any(kind not in WATCH_ROOT_KINDS for kind in kinds):
        raise ContainmentFormatError("openedRootKinds contains unknown root kind")
    if kinds != tuple(kind for kind in WATCH_ROOT_KINDS if kind in kinds):
        raise ContainmentFormatError("openedRootKinds must use canonical order")
    return kinds


def _map(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContainmentFormatError(f"{label} must be an object")
    actual = set(value)
    if actual != fields:
        raise ContainmentFormatError(
            f"{label} fields differ: missing={sorted(fields - actual)}, "
            f"extra={sorted(actual - fields)}"
        )
    return value


def _decode(value: bytes, label: str) -> Any:
    if not isinstance(value, bytes):
        raise ContainmentFormatError(f"{label} must be UTF-8 bytes")
    try:
        return json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ContainmentFormatError(f"invalid JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContainmentFormatError(f"{label} must be valid JSON") from error


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContainmentFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode()


def _schema(value: Any) -> int:
    if type(value) is not int or value != 1:
        raise ContainmentFormatError("schemaVersion must be integer 1")
    return value


def _decision_schema(value: Any, v2: bool) -> int:
    expected = 2 if v2 else 1
    if type(value) is not int or value != expected:
        raise ContainmentFormatError(f"decision schemaVersion must be integer {expected}")
    return value


def _fixed_int(value: Any, expected: int, label: str) -> int:
    if type(value) is not int or value != expected:
        raise ContainmentFormatError(f"{label} must be integer {expected}")
    return expected


def _bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ContainmentFormatError(f"{label} must be boolean")
    return value


def _nn(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ContainmentFormatError(f"{label} must be a non-negative integer")
    return value


def _pos(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContainmentFormatError(f"{label} must be a positive integer")
    return value


def _opt_nn(value: Any, label: str) -> int | None:
    return None if value is None else _nn(value, label)


def _opt_pos(value: Any, label: str) -> int | None:
    return None if value is None else _pos(value, label)


def _sha(value: Any, label: str) -> str:
    return _pattern(value, _SHA, label, "a lowercase SHA-256")


def _run(value: Any) -> str:
    return _pattern(value, _RUN, "runId", "a containment run ID")


def _pattern(
    value: Any,
    pattern: re.Pattern[str],
    label: str,
    description: str = "a canonical identifier",
) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ContainmentFormatError(f"{label} must be {description}")
    return value


def _opt_pattern(
    value: Any,
    pattern: re.Pattern[str],
    label: str,
) -> str | None:
    return None if value is None else _pattern(value, pattern, label)


def _enum(cls, value: Any, label: str):
    if not isinstance(value, str):
        raise ContainmentFormatError(f"{label} must be text")
    try:
        return cls(value)
    except ValueError as error:
        raise ContainmentFormatError(f"{label} has an unknown value") from error


def _fixed(value: Any, expected: str, label: str) -> str:
    if value != expected:
        raise ContainmentFormatError(f"{label} must equal {expected}")
    return expected


def _text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ContainmentFormatError(
            f"{label} must be non-blank text without controls"
        )
    return value


def _opt_text(value: Any, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _token(value: Any, label: str) -> str:
    token = _text(value, label)
    if (
        any(character in token for character in '\\/:*?<>|"')
        or token.endswith((".", " "))
        or token.split(".", 1)[0].casefold() in _RESERVED
    ):
        raise ContainmentFormatError(f"{label} is unsafe")
    return token


def _rel(value: Any, label: str) -> str:
    path = _text(value, label)
    if "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise ContainmentFormatError(f"{label} must be a safe relative path")
    for part in path.split("/"):
        if (
            not part
            or part in {".", ".."}
            or any(character in part for character in ':*?<>|"')
            or part.endswith((".", " "))
            or part.split(".", 1)[0].casefold() in _RESERVED
        ):
            raise ContainmentFormatError(
                f"{label} contains an unsafe path segment"
            )
    return path


def _abs(value: Any, label: str) -> str:
    path = _text(value, label)
    if (
        "/" in path
        or path.startswith("\\\\")
        or re.match(r"^[A-Za-z]:\\", path) is None
    ):
        raise ContainmentFormatError(
            f"{label} must be a canonical absolute Windows path"
        )
    parsed = PureWindowsPath(path)
    if (
        not parsed.is_absolute()
        or any(part in {".", ".."} for part in parsed.parts)
        or str(parsed) != path
    ):
        raise ContainmentFormatError(
            f"{label} must be a canonical absolute Windows path"
        )
    for part in parsed.parts[1:]:
        if (
            any(character in part for character in ':*?<>|"')
            or part.endswith((".", " "))
            or part.split(".", 1)[0].casefold() in _RESERVED
        ):
            raise ContainmentFormatError(
                f"{label} contains an unsafe path segment"
            )
    return path


def _texts(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContainmentFormatError(f"{label} must be an array")
    return tuple(_text(item, f"{label}[{index}]") for index, item in enumerate(value))


def _sorted_rel(value: Any, label: str) -> tuple[str, ...]:
    return _sorted(value, label, _rel)


def _sorted_text(value: Any, label: str) -> tuple[str, ...]:
    return _sorted(value, label, _text)


def _sorted(value: Any, label: str, parser) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContainmentFormatError(f"{label} must be an array")
    items = tuple(
        parser(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )
    if len({item.casefold() for item in items}) != len(items):
        raise ContainmentFormatError(
            f"{label} must not contain case-insensitive duplicates"
        )
    if items != tuple(sorted(items, key=lambda item: (item.casefold(), item))):
        raise ContainmentFormatError(
            f"{label} must use canonical case-insensitive order"
        )
    return items


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ContainmentFormatError("scenarioResultIds must be an array")
    identifiers = tuple(
        _pattern(item, _RESULT_ID, f"scenarioResultIds[{index}]")
        for index, item in enumerate(value)
    )
    if len(identifiers) != len(set(identifiers)):
        raise ContainmentFormatError(
            "scenarioResultIds must not contain duplicates"
        )
    return identifiers
