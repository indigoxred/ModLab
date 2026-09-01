"""Crash-safe orchestration and fail-closed adjudication for MO2 containment."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import stat
from typing import Mapping
import uuid

from modlab.adapters.mo2.processes import inspect_mo2_processes
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.workspace import workspace_layout

from .mo2_containment_fixtures import (
    ContainmentFixture,
    prepare_containment_fixture,
)
from .mo2_containment_model import (
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
    WatchOutcome,
)
from .mo2_containment_serialization import (
    ContainmentFormatError,
    capability_decision_to_bytes,
    scenario_result_id_for,
    scenario_result_to_bytes,
    watch_outcome_id_for,
)
from .mo2_containment_store import (
    ContainmentStore,
    ContainmentStoreError,
    ContainmentStoreNotFound,
)
from .windows_integrity import (
    IntegrityLevel,
    inspect_path_integrity,
    inspect_process_integrity,
    launch_low_integrity_process,
    set_low_integrity_tree,
    source_integrity_allowed,
    stage_integrity_allowed,
    to_integrity_observation,
)
from .windows_junction import (
    ContainmentSafetyError,
    adopt_unique_staged_mod,
    inspect_junction,
    stable_tree_identity,
)
from . import windows_watch as _windows_watch
from .windows_watch import start_watch, stop_watch, watch_root
from .windows_watch_protocol import (
    LAUNCH_NAME,
    ROOT_KINDS,
    WatchRequest,
    watch_request_from_bytes,
    worker_launch_from_bytes,
)


_SCHEMA_VERSION = 1
_MECHANISM = "isolated-low-integrity-junction-projection-v1"
_PROTECTED_NAME = "Protected Existing"
_EXPECTED_NEW = {
    ContainmentScenario.NEW_FOLDER: "ModLab Spike New",
    ContainmentScenario.MERGE_EXISTING: _PROTECTED_NAME,
    ContainmentScenario.REPLACE_EXISTING: _PROTECTED_NAME,
    ContainmentScenario.FOMOD_DEPENDENCY: "ModLab Spike FOMOD",
}
_EXPECTED_OUTPUTS = {
    ContainmentScenario.NEW_FOLDER: ("meshes/new-folder.bin",),
    ContainmentScenario.FOMOD_DEPENDENCY: (
        "always.txt",
        "dependency-seen.txt",
    ),
}
_LAUNCH_EVIDENCE: dict[tuple[str, str, ContainmentScenario], ProcessEvidence] = {}


class ContainmentServiceError(RuntimeError):
    """The scenario cannot advance without weakening its evidence boundary."""


@dataclass(frozen=True)
class FixtureRecord:
    scenario: ContainmentScenario
    run_root: Path
    source_root: Path
    stage_root: Path
    archive_path: Path
    source_mods: Path
    stage_mods: Path
    source_lab_modlist: Path
    source_play_modlist: Path
    source_downloads: Path
    source_overwrite: Path
    bounded_game: Path
    stage_app: Path
    stage_downloads: Path
    stage_profiles: Path
    stage_overwrite: Path
    stage_cache: Path
    stage_logs: Path
    stage_environment: Mapping[str, str]
    external_watch_roots: tuple[tuple[str, Path], ...]
    before_names: tuple[str, ...]

    @property
    def executable(self) -> Path:
        return self.stage_app / "ModOrganizer.exe"


@dataclass(frozen=True)
class ScenarioEvidence:
    run_id: str
    scenario: ContainmentScenario
    protected_before: ProtectedState
    protected_after: ProtectedState
    watch_outcome: WatchOutcome
    mo2_process: ProcessEvidence | None
    source_integrity: IntegrityObservation
    stage_integrity: IntegrityObservation
    scenario_started: bool
    fresh_retry_eligible: bool
    projection_count: int
    projection_targets_verified: bool
    projection_payload_bytes_copied: int
    production_backup_names: tuple[str, ...]
    staging_new_names: tuple[str, ...]
    staging_output_names: tuple[str, ...]
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    safety_reasons: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryCleanupEvidence:
    watch_outcome: WatchOutcome | None
    protected_after: ProtectedState
    source_integrity: IntegrityObservation
    stage_integrity: IntegrityObservation
    projection_count: int
    projection_targets_verified: bool
    projection_payload_bytes_copied: int
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class _ProjectionEvidence:
    protected_after: ProtectedState
    projection_count: int
    projection_targets_verified: bool
    production_backup_names: tuple[str, ...]
    staging_new_names: tuple[str, ...]
    staging_output_names: tuple[str, ...]
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    safety_reasons: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]


def evaluate_scenario(value: ScenarioEvidence) -> ScenarioResult:
    """Apply exact scenario policy with Failed > Incomplete > Passed precedence."""
    if not isinstance(value, ScenarioEvidence):
        raise ContainmentServiceError("scenario evaluation requires ScenarioEvidence")
    outcome = value.watch_outcome
    if outcome.run_id != value.run_id or outcome.scenario is not value.scenario:
        raise ContainmentServiceError("watch outcome is not bound to the scenario")

    violations = set(value.safety_reasons)
    incomplete = set(value.incomplete_reasons)
    if outcome.events:
        violations.add("forbidden-watcher-event")
    if value.protected_before != value.protected_after:
        violations.add("protected-state-changed")
    if outcome.evidence_completion is not WatchEvidenceCompletion.COMPLETED:
        incomplete.add("watch-evidence-incomplete")
    if value.mo2_process is None:
        incomplete.add("mo2-process-evidence-missing")
    else:
        expected_executable = Path(value.mo2_process.working_directory) / "ModOrganizer.exe"
        if (
            value.mo2_process.integrity is not IntegrityObservation.LOW
            or value.mo2_process.executable_version != "2.5.2.0"
            or tuple(value.mo2_process.arguments) != ("--profile", "ModLab - Lab")
            or not _same_path(value.mo2_process.executable, expected_executable)
        ):
            violations.add("mo2-process-evidence-invalid")
    if value.source_integrity not in {
        IntegrityObservation.MEDIUM,
        IntegrityObservation.HIGH,
        IntegrityObservation.SYSTEM,
    }:
        violations.add("source-integrity-invalid")
    if value.stage_integrity is not IntegrityObservation.LOW:
        violations.add("stage-integrity-invalid")
    if value.projection_count <= 0:
        violations.add("projection-count-invalid")
    if not value.projection_targets_verified:
        violations.add("projection-target-changed")
    if value.projection_payload_bytes_copied != 0:
        violations.add("projection-payload-copied")
    if value.production_backup_names:
        violations.add("production-backup-created")
    if not value.source_restored_after_quarantine:
        violations.add("source-not-restored-after-quarantine")

    expected_name = _EXPECTED_NEW[value.scenario]
    expected_outputs = _EXPECTED_OUTPUTS.get(value.scenario, ())
    adoption = value.scenario in _EXPECTED_OUTPUTS
    if adoption:
        if value.staging_new_names != (expected_name,):
            violations.add("staging-new-folder-set-invalid")
        if value.staging_output_names != expected_outputs:
            violations.add("staging-output-set-invalid")
        if (
            value.adopted_name != expected_name
            or value.adopted_tree is None
            or value.adopted_integrity is not IntegrityObservation.MEDIUM
        ):
            violations.add("adoption-proof-invalid")
    elif (
        value.staging_new_names
        or value.staging_output_names
        or value.adopted_name is not None
        or value.adopted_tree is not None
        or value.adopted_integrity is not None
    ):
        violations.add("unexpected-staging-output")

    positive_breach = bool(outcome.events) or value.protected_before != value.protected_after
    if violations and positive_breach:
        scenario_outcome = ScenarioOutcome.FAILED
        reasons = tuple(sorted(violations | incomplete))
        retry = False
    elif violations or incomplete:
        scenario_outcome = ScenarioOutcome.INCOMPLETE
        reasons = tuple(sorted(violations | incomplete))
        retry = bool(
            value.fresh_retry_eligible
            and not value.scenario_started
            and outcome.evidence_completion is WatchEvidenceCompletion.INCOMPLETE
            and "controller-session-lost" in outcome.reason_codes
        )
    else:
        scenario_outcome = ScenarioOutcome.PASSED
        reasons = ()
        retry = False

    result = ScenarioResult(
        schema_version=_SCHEMA_VERSION,
        run_id=value.run_id,
        scenario=value.scenario,
        outcome=scenario_outcome,
        protected_before=value.protected_before,
        protected_after=value.protected_after,
        mo2_process=value.mo2_process,
        source_integrity=value.source_integrity,
        stage_integrity=value.stage_integrity,
        watch_outcome_id=watch_outcome_id_for(outcome),
        watch_evidence_completion=outcome.evidence_completion,
        scenario_started=value.scenario_started,
        fresh_retry_eligible=retry,
        watcher_events=outcome.events,
        projection_count=value.projection_count,
        projection_targets_verified=value.projection_targets_verified,
        projection_payload_bytes_copied=value.projection_payload_bytes_copied,
        production_backup_names=tuple(sorted(set(value.production_backup_names))),
        staging_new_names=tuple(sorted(set(value.staging_new_names))),
        staging_output_names=tuple(sorted(set(value.staging_output_names))),
        adopted_name=value.adopted_name,
        adopted_tree=value.adopted_tree,
        adopted_integrity=value.adopted_integrity,
        source_restored_after_quarantine=value.source_restored_after_quarantine,
        reasons=reasons,
    )
    try:
        scenario_result_to_bytes(result, outcome)
    except ContainmentFormatError as error:
        raise ContainmentServiceError(f"scenario evaluation is not serializable: {error}") from error
    return result


def adjudicate_results(
    scenario_results: tuple[ScenarioResult, ...],
    watch_outcomes: tuple[WatchOutcome, ...],
) -> CapabilityDecision:
    """Deep-bind results and retain the worst immutable result per scenario."""
    results = tuple(scenario_results)
    outcomes = tuple(watch_outcomes)
    if len(results) != len(outcomes):
        raise ContainmentServiceError("result/outcome evidence counts differ")
    if not results:
        raise ContainmentServiceError("adjudication requires a run ID")
    run_id = results[0].run_id
    grouped: dict[ContainmentScenario, list[tuple[ScenarioResult, WatchOutcome]]] = {}
    for result, outcome in zip(results, outcomes, strict=True):
        if result.run_id != run_id or outcome.run_id != run_id:
            raise ContainmentServiceError("adjudication evidence spans multiple runs")
        if result.scenario is not outcome.scenario:
            raise ContainmentServiceError("result/outcome scenario binding differs")
        try:
            scenario_result_to_bytes(result, outcome)
        except ContainmentFormatError as error:
            raise ContainmentServiceError(f"invalid adjudication evidence: {error}") from error
        grouped.setdefault(result.scenario, []).append((result, outcome))

    rank = {
        ScenarioOutcome.PASSED: 0,
        ScenarioOutcome.INCOMPLETE: 1,
        ScenarioOutcome.FAILED: 2,
    }
    selected = tuple(
        max(grouped[scenario], key=lambda pair: rank[pair[0].outcome])
        for scenario in ContainmentScenario
        if scenario in grouped
    )
    selected_results = tuple(item[0] for item in selected)
    selected_outcomes = tuple(item[1] for item in selected)
    identifiers = tuple(
        scenario_result_id_for(result, outcome)
        for result, outcome in selected
    )
    if any(result.outcome is ScenarioOutcome.FAILED for result in selected_results):
        verdict = CapabilityVerdict.REJECTED
        reasons = ("containment-breach",)
    elif (
        len(selected_results) != len(ContainmentScenario)
        or any(result.outcome is not ScenarioOutcome.PASSED for result in selected_results)
    ):
        verdict = CapabilityVerdict.INCOMPLETE
        reasons = ("scenario-evidence-incomplete",)
    else:
        verdict = CapabilityVerdict.SUPPORTED
        reasons = ()
    decision = CapabilityDecision(
        _SCHEMA_VERSION,
        run_id,
        _MECHANISM,
        verdict,
        identifiers,
        reasons,
    )
    try:
        capability_decision_to_bytes(decision, selected_results, selected_outcomes)
    except ContainmentFormatError as error:
        raise ContainmentServiceError(f"capability decision is invalid: {error}") from error
    return decision


def prepare_run(
    source_workspace: Path,
    mo2_artifact_id: str,
    steam_root: Path,
    validation_root: Path,
) -> str:
    """Create four independent disposable fixtures and one immutable request."""
    source = Path(source_workspace).expanduser().absolute()
    validation = Path(validation_root).expanduser().absolute()
    expected_validation = workspace_layout(source).mo2_containment_validation
    if not _same_path(validation, expected_validation):
        raise ContainmentServiceError("validation root must be the workspace containment root")
    fixtures = tuple(
        prepare_containment_fixture(
            source,
            mo2_artifact_id,
            Path(steam_root),
            validation,
            scenario,
        )
        for scenario in ContainmentScenario
    )
    run_id = "containment-run:" + uuid.uuid4().hex
    records = tuple(
        _fixture_record(fixture, Path(steam_root))
        for fixture in fixtures
    )
    document = {
        "schemaVersion": _SCHEMA_VERSION,
        "runId": run_id,
        "sourceWorkspace": str(source),
        "mo2ArtifactId": mo2_artifact_id,
        "steamRoot": str(Path(steam_root).expanduser().absolute()),
        "scenarios": [_record_document(record) for record in records],
    }
    ContainmentStore(validation).write_request(run_id, document)
    return run_id


def arm_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioJournal:
    """Capture stable pre-state and stop only after the watcher is ready."""
    store = ContainmentStore(validation_root)
    record = _load_fixture_record(store, run_id, scenario)
    before = _capture_protected(record)
    source_level = inspect_path_integrity(record.source_root)
    stage_level = inspect_path_integrity(record.stage_root)
    if not source_integrity_allowed(source_level):
        raise ContainmentServiceError("source integrity is not Medium or higher")
    if not stage_integrity_allowed(stage_level):
        raise ContainmentServiceError("stage integrity is not Low")
    projection_count, targets_verified = _projection_state(record)
    if not targets_verified or projection_count <= 0:
        raise ContainmentServiceError("stage projection is not exact before arming")
    store.write_protected_state(run_id, scenario, "before", before)
    journal = store.create(
        ScenarioJournal(
            _SCHEMA_VERSION,
            run_id,
            scenario,
            ScenarioState.PREPARED,
            str(record.source_root),
            str(record.stage_root),
            str(record.archive_path),
            _PROTECTED_NAME,
            _EXPECTED_NEW[scenario],
            before,
            None,
            None,
            None,
        )
    )
    evidence_root = store.watch_path(run_id, scenario)
    roots = _watch_roots(record)
    request = WatchRequest(
        request_id="watch-request:" + secrets.token_hex(32),
        session_id="watch-session:" + secrets.token_hex(32),
        run_id=run_id,
        scenario=scenario,
        evidence_root=evidence_root,
        stop_token_path=evidence_root / "stop.token",
        roots=roots,
    )
    try:
        worker_pid = start_watch(request)
    except (OSError, RuntimeError) as error:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error=f"watch startup failed: {error}",
        )
        raise ContainmentServiceError(f"watch startup failed: {error}") from error
    return store.transition(journal, ScenarioState.ARMED, monitor_pid=worker_pid)


def launch_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioJournal:
    """Persist ScenarioStarted before launching the exact Low-integrity child."""
    store = ContainmentStore(validation_root)
    record = _load_fixture_record(store, run_id, scenario)
    journal = store.load_journal(run_id, scenario)
    if journal.state is not ScenarioState.ARMED:
        raise ContainmentServiceError(
            f"launch requires Armed journal, observed {journal.state.value}"
        )
    if not _watcher_live(
        store.watch_path(run_id, scenario) / "request.json",
        journal.monitor_pid,
    ):
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="watcher liveness could not be verified before launch",
        )
        raise ContainmentServiceError("watcher liveness could not be verified before launch")
    if _capture_protected(record) != journal.protected_before:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="protected state changed before ScenarioStarted",
        )
        raise ContainmentServiceError("protected state changed before ScenarioStarted")
    started = store.transition(journal, ScenarioState.SCENARIO_STARTED)
    version = read_windows_file_version(record.executable)
    if version != "2.5.2.0":
        store.transition(
            started,
            ScenarioState.RECOVERY_REQUIRED,
            error="post-ScenarioStarted: stage MO2 executable version is not 2.5.2.0",
        )
        raise ContainmentServiceError("stage MO2 executable version is not 2.5.2.0")
    try:
        launch = launch_low_integrity_process(
            record.executable,
            ("--profile", "ModLab - Lab"),
            record.stage_app,
            record.stage_environment,
        )
        observed_integrity = inspect_process_integrity(launch.pid)
        observation = inspect_mo2_processes(record.stage_root)
        exact = tuple(item for item in observation.relevant if item.pid == launch.pid)
        if (
            launch.integrity is not IntegrityLevel.LOW
            or observed_integrity is not IntegrityLevel.LOW
            or not observation.complete
            or len(exact) != 1
            or not _same_path(exact[0].executable_path, record.executable)
            or not _same_path(launch.executable, record.executable)
            or launch.arguments != ("--profile", "ModLab - Lab")
            or not _same_path(launch.working_directory, record.stage_app)
        ):
            raise ContainmentServiceError("launched MO2 identity/integrity proof failed")
    except BaseException as error:
        mo2_pid = getattr(locals().get("launch"), "pid", None)
        store.transition(
            started,
            ScenarioState.RECOVERY_REQUIRED,
            mo2_pid=mo2_pid,
            error=f"post-ScenarioStarted launch failed: {error}",
        )
        if isinstance(error, ContainmentServiceError):
            raise
        raise ContainmentServiceError(f"post-ScenarioStarted launch failed: {error}") from error
    process = ProcessEvidence(
        launch.pid,
        str(record.executable),
        version,
        launch.arguments,
        str(record.stage_app),
        to_integrity_observation(observed_integrity),
    )
    _LAUNCH_EVIDENCE[_launch_key(store, run_id, scenario)] = process
    return store.transition(started, ScenarioState.LAUNCHED, mo2_pid=launch.pid)


def capture_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioResult:
    """Stop the same-controller watcher, bind its outcome, and capture final policy."""
    store = ContainmentStore(validation_root)
    record = _load_fixture_record(store, run_id, scenario)
    journal = store.load_journal(run_id, scenario)
    if journal.state is not ScenarioState.LAUNCHED:
        raise ContainmentServiceError(
            f"capture requires Launched journal, observed {journal.state.value}"
        )
    observation = inspect_mo2_processes(record.stage_root)
    if not observation.complete:
        raise ContainmentServiceError("MO2 process liveness is uncertain")
    if observation.relevant:
        raise ContainmentServiceError("MO2 must be closed before capture")
    process = _LAUNCH_EVIDENCE.get(_launch_key(store, run_id, scenario))
    if process is None or process.pid != journal.mo2_pid:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="same-controller launch evidence is unavailable",
        )
        raise ContainmentServiceError("same-controller launch evidence is unavailable")

    request_path = store.watch_path(run_id, scenario) / "request.json"
    receipt = stop_watch(request_path)
    if receipt.watch_outcome_id is None:
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="watch outcome was not durably published",
        )
        raise ContainmentServiceError("watch outcome was not durably published")
    outcome = store.load_watch_outcome(run_id, scenario, receipt.watch_outcome_id)
    if (
        receipt.run_id != run_id
        or receipt.scenario is not scenario
        or receipt.request_id != outcome.request_id
        or receipt.session_id != outcome.session_id
        or receipt.worker_pid != outcome.worker_pid
        or receipt.request_bytes_sha256 != outcome.request_sha256
    ):
        store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error="watch receipt/outcome binding mismatch",
        )
        raise ContainmentServiceError("watch receipt/outcome binding mismatch")

    post_mo2 = _capture_protected(record)
    projection = _finalize_projection(
        store,
        run_id,
        record,
        journal.protected_before,
        post_mo2,
    )
    store.write_protected_state(
        run_id,
        scenario,
        "after",
        projection.protected_after,
    )
    source_observation = _integrity_observation(record.source_root)
    stage_observation = _integrity_observation(record.stage_root)
    result = evaluate_scenario(
        ScenarioEvidence(
            run_id,
            scenario,
            journal.protected_before,
            projection.protected_after,
            outcome,
            process,
            source_observation,
            stage_observation,
            True,
            False,
            projection.projection_count,
            projection.projection_targets_verified,
            0,
            projection.production_backup_names,
            projection.staging_new_names,
            projection.staging_output_names,
            projection.adopted_name,
            projection.adopted_tree,
            projection.adopted_integrity,
            projection.source_restored_after_quarantine,
            projection.safety_reasons,
            projection.incomplete_reasons,
        )
    )
    store.write_result(result)
    store.transition(journal, ScenarioState.CAPTURED)
    _LAUNCH_EVIDENCE.pop(_launch_key(store, run_id, scenario), None)
    return result


def recover_scenario(
    validation_root: Path,
    run_id: str,
    scenario: ContainmentScenario,
) -> ScenarioRecovery:
    """Perform cleanup only; never return a journal or resume a success path."""
    store = ContainmentStore(validation_root)
    journal = store.load_journal(run_id, scenario)
    if journal.state is ScenarioState.CAPTURED:
        raise ContainmentServiceError("Captured scenario does not require recovery")
    if journal.state is not ScenarioState.RECOVERY_REQUIRED:
        journal = store.transition(
            journal,
            ScenarioState.RECOVERY_REQUIRED,
            error=f"interrupted in {journal.state.value}",
        )
    cleanup = _perform_recovery_cleanup(store, _load_fixture_record(store, run_id, scenario), journal)
    blockers = tuple(sorted(set(cleanup.blockers)))
    result_id: str | None = None
    pre_scenario = journal.mo2_pid is None and "ScenarioStarted" not in (journal.error or "")
    if blockers or cleanup.watch_outcome is None:
        if cleanup.watch_outcome is None:
            blockers = tuple(sorted(set((*blockers, "durable-watch-outcome-unavailable"))))
        recovery = ScenarioRecovery(
            _SCHEMA_VERSION,
            run_id,
            scenario,
            store.journal_id_for(journal),
            None,
            ScenarioCleanupStatus.REFUSED,
            False,
            blockers,
        )
        store.write_recovery(recovery)
        return recovery

    outcome = cleanup.watch_outcome
    if watch_outcome_id_for(outcome) != watch_outcome_id_for(
        store.load_watch_outcome(run_id, scenario, watch_outcome_id_for(outcome))
    ):
        raise ContainmentServiceError("recovery watch outcome changed after cleanup")
    try:
        existing_result = store.load_result(run_id, scenario)
    except ContainmentStoreNotFound:
        existing_result = None
    if existing_result is not None:
        if existing_result.watch_outcome_id != watch_outcome_id_for(outcome):
            raise ContainmentServiceError(
                "existing scenario result is bound to another watch outcome"
            )
        existing_id = scenario_result_id_for(existing_result, outcome)
        retry_existing = bool(
            existing_result.fresh_retry_eligible
            and not existing_result.scenario_started
        )
        recovery = ScenarioRecovery(
            _SCHEMA_VERSION,
            run_id,
            scenario,
            store.journal_id_for(journal),
            existing_id,
            ScenarioCleanupStatus.SUCCEEDED,
            retry_existing,
            (),
        )
        store.write_recovery(recovery)
        _LAUNCH_EVIDENCE.pop(_launch_key(store, run_id, scenario), None)
        return recovery
    retry = bool(
        pre_scenario
        and outcome.evidence_completion is WatchEvidenceCompletion.INCOMPLETE
        and "controller-session-lost" in outcome.reason_codes
        and cleanup.protected_after == journal.protected_before
    )
    prior_process = _LAUNCH_EVIDENCE.get(_launch_key(store, run_id, scenario))
    result = evaluate_scenario(
        ScenarioEvidence(
            run_id,
            scenario,
            journal.protected_before,
            cleanup.protected_after,
            outcome,
            prior_process,
            cleanup.source_integrity,
            cleanup.stage_integrity,
            not pre_scenario,
            retry,
            cleanup.projection_count,
            cleanup.projection_targets_verified,
            cleanup.projection_payload_bytes_copied,
            (),
            (),
            (),
            None,
            None,
            None,
            cleanup.protected_after == journal.protected_before,
            (),
            ("interrupted-scenario-cleaned",),
        )
    )
    written = store.write_result(result)
    result_id = written.content_id
    recovery = ScenarioRecovery(
        _SCHEMA_VERSION,
        run_id,
        scenario,
        store.journal_id_for(journal),
        result_id,
        ScenarioCleanupStatus.SUCCEEDED,
        retry,
        (),
    )
    store.write_recovery(recovery)
    _LAUNCH_EVIDENCE.pop(_launch_key(store, run_id, scenario), None)
    return recovery


def adjudicate_run(validation_root: Path, run_id: str) -> CapabilityDecision:
    """Resolve every retained result and write one immutable capability decision."""
    store = ContainmentStore(validation_root)
    results: list[ScenarioResult] = []
    outcomes: list[WatchOutcome] = []
    for scenario in ContainmentScenario:
        try:
            result = store.load_result(run_id, scenario)
        except ContainmentStoreNotFound:
            continue
        outcome = store.load_watch_outcome(run_id, scenario, result.watch_outcome_id)
        results.append(result)
        outcomes.append(outcome)
    if not results:
        decision = CapabilityDecision(
            _SCHEMA_VERSION,
            run_id,
            _MECHANISM,
            CapabilityVerdict.INCOMPLETE,
            (),
            ("scenario-evidence-unavailable",),
        )
    else:
        decision = adjudicate_results(tuple(results), tuple(outcomes))
    store.write_decision(decision)
    return decision


def _fixture_record(fixture: ContainmentFixture, steam_root: Path) -> FixtureRecord:
    scenario = fixture.scenario
    archive = {
        ContainmentScenario.NEW_FOLDER: fixture.archives.new_folder,
        ContainmentScenario.MERGE_EXISTING: fixture.archives.overwrite_probe,
        ContainmentScenario.REPLACE_EXISTING: fixture.archives.overwrite_probe,
        ContainmentScenario.FOMOD_DEPENDENCY: fixture.archives.fomod_dependency,
    }[scenario]
    game = Path(steam_root).expanduser().absolute() / "steamapps" / "common" / "Skyrim Special Edition"
    before_names = _direct_names(fixture.source_mods)
    return FixtureRecord(
        scenario,
        fixture.run_root,
        fixture.source_layout.skyrim_mo2,
        fixture.stage_layout.skyrim_mo2,
        archive,
        fixture.source_mods,
        fixture.stage_mods,
        fixture.source_lab_modlist,
        fixture.source_play_modlist,
        fixture.source_layout.skyrim_mo2_downloads,
        fixture.source_layout.skyrim_mo2_overwrite,
        game,
        fixture.stage_layout.skyrim_mo2_app,
        fixture.stage_layout.skyrim_mo2_downloads,
        fixture.stage_layout.skyrim_mo2_profiles,
        fixture.stage_layout.skyrim_mo2_overwrite,
        fixture.stage_cache,
        fixture.stage_logs,
        dict(fixture.stage_environment),
        fixture.external_watch_roots,
        before_names,
    )


def _record_document(record: FixtureRecord) -> dict[str, object]:
    return {
        "scenario": record.scenario.value,
        "runRoot": str(record.run_root),
        "sourceRoot": str(record.source_root),
        "stageRoot": str(record.stage_root),
        "archivePath": str(record.archive_path),
        "sourceMods": str(record.source_mods),
        "stageMods": str(record.stage_mods),
        "sourceLabModlist": str(record.source_lab_modlist),
        "sourcePlayModlist": str(record.source_play_modlist),
        "sourceDownloads": str(record.source_downloads),
        "sourceOverwrite": str(record.source_overwrite),
        "boundedGame": str(record.bounded_game),
        "stageApp": str(record.stage_app),
        "stageDownloads": str(record.stage_downloads),
        "stageProfiles": str(record.stage_profiles),
        "stageOverwrite": str(record.stage_overwrite),
        "stageCache": str(record.stage_cache),
        "stageLogs": str(record.stage_logs),
        "stageEnvironment": dict(sorted(record.stage_environment.items())),
        "externalWatchRoots": [
            {"rootKind": kind, "path": str(path)}
            for kind, path in record.external_watch_roots
        ],
        "beforeNames": list(record.before_names),
    }


def _load_fixture_record(
    store: ContainmentStore,
    run_id: str,
    scenario: ContainmentScenario,
) -> FixtureRecord:
    document = store.load_request(run_id)
    request_fields = {
        "schemaVersion",
        "runId",
        "sourceWorkspace",
        "mo2ArtifactId",
        "steamRoot",
        "scenarios",
    }
    if set(document) != request_fields:
        raise ContainmentServiceError("request fields are not exact")
    if (
        document["schemaVersion"] != _SCHEMA_VERSION
        or document["runId"] != run_id
        or type(document["sourceWorkspace"]) is not str
        or type(document["mo2ArtifactId"]) is not str
        or not document["mo2ArtifactId"]
        or type(document["steamRoot"]) is not str
    ):
        raise ContainmentServiceError("request identity values are malformed")
    rows = document.get("scenarios")
    if (
        type(rows) is not list
        or len(rows) != len(ContainmentScenario)
        or tuple(
            row.get("scenario") if type(row) is dict else None
            for row in rows
        )
        != tuple(item.value for item in ContainmentScenario)
    ):
        raise ContainmentServiceError("request scenarios are unavailable")
    matches = [row for row in rows if type(row) is dict and row.get("scenario") == scenario.value]
    if len(matches) != 1:
        raise ContainmentServiceError("request does not contain one exact scenario record")
    row = matches[0]
    expected = {
        "scenario", "runRoot", "sourceRoot", "stageRoot", "archivePath",
        "sourceMods", "stageMods", "sourceLabModlist", "sourcePlayModlist",
        "sourceDownloads", "sourceOverwrite", "boundedGame", "stageApp",
        "stageDownloads", "stageProfiles", "stageOverwrite", "stageCache",
        "stageLogs", "stageEnvironment", "externalWatchRoots", "beforeNames",
    }
    if set(row) != expected:
        raise ContainmentServiceError("scenario request fields are not exact")
    environment = row["stageEnvironment"]
    roots = row["externalWatchRoots"]
    names = row["beforeNames"]
    if (
        type(environment) is not dict
        or any(type(key) is not str or type(value) is not str for key, value in environment.items())
        or type(roots) is not list
        or type(names) is not list
        or any(type(name) is not str for name in names)
    ):
        raise ContainmentServiceError("scenario request values are malformed")
    external: list[tuple[str, Path]] = []
    for item in roots:
        if type(item) is not dict or set(item) != {"rootKind", "path"}:
            raise ContainmentServiceError("external watch root is malformed")
        external.append((str(item["rootKind"]), Path(str(item["path"]))))
    path_names = (
        "runRoot", "sourceRoot", "stageRoot", "archivePath", "sourceMods",
        "stageMods", "sourceLabModlist", "sourcePlayModlist", "sourceDownloads",
        "sourceOverwrite", "boundedGame", "stageApp", "stageDownloads",
        "stageProfiles", "stageOverwrite", "stageCache", "stageLogs",
    )
    paths = {name: Path(str(row[name])) for name in path_names}
    if any(not path.is_absolute() for path in paths.values()):
        raise ContainmentServiceError("scenario request paths must be absolute")
    if not _beneath(store.root, paths["runRoot"]):
        raise ContainmentServiceError("scenario fixture root escapes validation root")
    confined_paths = tuple(
        paths[name]
        for name in path_names
        if name not in {"runRoot", "boundedGame"}
    )
    if any(not _beneath(paths["runRoot"], path) for path in confined_paths):
        raise ContainmentServiceError("scenario fixture path escapes its run root")
    if tuple(kind for kind, _path in external) != ROOT_KINDS[-2:]:
        raise ContainmentServiceError("external watch roots are not exact")
    for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        value = environment.get(name)
        if type(value) is not str:
            raise ContainmentServiceError("contained stage environment is incomplete")
        environment_path = Path(value)
        if not environment_path.is_absolute() or not _beneath(paths["runRoot"], environment_path):
            raise ContainmentServiceError("contained stage environment escapes its run root")
    supplied_names = tuple(names)
    if (
        supplied_names != (_PROTECTED_NAME,)
        or
        supplied_names != tuple(sorted(supplied_names, key=lambda item: (item.casefold(), item)))
        or len({name.casefold() for name in supplied_names}) != len(supplied_names)
    ):
        raise ContainmentServiceError("beforeNames must be canonical and unique")
    return FixtureRecord(
        scenario,
        paths["runRoot"], paths["sourceRoot"], paths["stageRoot"],
        paths["archivePath"], paths["sourceMods"], paths["stageMods"],
        paths["sourceLabModlist"], paths["sourcePlayModlist"],
        paths["sourceDownloads"], paths["sourceOverwrite"], paths["boundedGame"],
        paths["stageApp"], paths["stageDownloads"], paths["stageProfiles"],
        paths["stageOverwrite"], paths["stageCache"], paths["stageLogs"],
        dict(environment), tuple(external), supplied_names,
    )


def _capture_protected(record: FixtureRecord) -> ProtectedState:
    return ProtectedState(
        stable_tree_identity(record.source_mods, required_equal_passes=2),
        _stable_file_sha256(record.source_lab_modlist),
        _stable_file_sha256(record.source_play_modlist),
        stable_tree_identity(record.source_downloads, required_equal_passes=2),
        stable_tree_identity(record.source_overwrite, required_equal_passes=2),
        stable_tree_identity(record.bounded_game, required_equal_passes=2),
    )


def _stable_file_sha256(path: Path) -> str:
    def observe() -> tuple[str, int, int]:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise ContainmentServiceError(f"protected file is not direct: {path}")
        data = path.read_bytes()
        after = path.lstat()
        if (metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ContainmentServiceError(f"protected file changed while hashing: {path}")
        return hashlib.sha256(data).hexdigest(), len(data), after.st_mtime_ns
    first = observe()
    second = observe()
    if first != second:
        raise ContainmentServiceError(f"protected file was not stable: {path}")
    return first[0]


def _watch_roots(record: FixtureRecord):
    physical = (
        ("SourceMods", record.source_mods),
        ("LabProfile", record.source_lab_modlist.parent),
        ("PlayProfile", record.source_play_modlist.parent),
        ("Downloads", record.source_downloads),
        ("Overwrite", record.source_overwrite),
        ("BoundedGame", record.bounded_game),
        *record.external_watch_roots,
    )
    if tuple(kind for kind, _ in physical) != ROOT_KINDS:
        raise ContainmentServiceError("fixture does not provide all eight exact watch roots")
    return tuple(watch_root(kind, path) for kind, path in physical)


def _projection_state(
    record: FixtureRecord,
    *,
    allow_new: bool = False,
) -> tuple[int, bool]:
    stage_names = _direct_names(record.stage_mods)
    if allow_new:
        if any(name not in stage_names for name in record.before_names):
            return len(record.before_names), False
    elif stage_names != record.before_names:
        return len(stage_names), False
    for name in record.before_names:
        try:
            evidence = inspect_junction(record.stage_mods / name)
        except (OSError, ContainmentSafetyError):
            return len(record.before_names), False
        if not _same_path(evidence.target_path, record.source_mods / name):
            return len(record.before_names), False
    return len(record.before_names), True


def _changed_projection_names(record: FixtureRecord) -> tuple[str, ...]:
    stage_names = set(_direct_names(record.stage_mods))
    changed: list[str] = []
    for name in record.before_names:
        if name not in stage_names:
            continue
        try:
            evidence = inspect_junction(record.stage_mods / name)
        except (OSError, ContainmentSafetyError):
            changed.append(name)
            continue
        if not _same_path(evidence.target_path, record.source_mods / name):
            changed.append(name)
    return tuple(changed)


def _finalize_projection(
    store: ContainmentStore,
    run_id: str,
    record: FixtureRecord,
    before: ProtectedState,
    post_mo2: ProtectedState,
) -> _ProjectionEvidence:
    stage_names = _direct_names(record.stage_mods)
    source_names = _direct_names(record.source_mods)
    production_backups = tuple(name for name in source_names if name not in record.before_names)
    new_names = tuple(name for name in stage_names if name not in record.before_names)
    projection_count, targets_verified = _projection_state(record, allow_new=True)
    safety: list[str] = []
    incomplete: list[str] = []
    if production_backups:
        safety.append("production-backup-created")
    if not targets_verified:
        safety.append("projection-target-changed")
    adopted_name: str | None = None
    adopted_tree: TreeIdentity | None = None
    adopted_integrity: IntegrityObservation | None = None
    outputs: tuple[str, ...] = ()
    final = post_mo2
    restored = post_mo2 == before
    quarantine = store.quarantine_path(run_id) / record.scenario.value
    quarantine.mkdir(exist_ok=True)

    if record.scenario in _EXPECTED_OUTPUTS:
        expected = _EXPECTED_NEW[record.scenario]
        adoption = None
        if post_mo2 != before:
            safety.append("protected-state-changed-before-adoption")
            quarantine_names = tuple(
                dict.fromkeys(
                    (*_changed_projection_names(record), *new_names)
                )
            )
            for name in quarantine_names:
                try:
                    _quarantine_exact(record.stage_mods / name, quarantine)
                except (OSError, ContainmentSafetyError):
                    incomplete.append("staging-quarantine-failed")
        else:
            try:
                adoption = adopt_unique_staged_mod(
                    stage_mods=record.stage_mods,
                    source_mods=record.source_mods,
                    expected_name=expected,
                    before_names=record.before_names,
                    quarantine_root=quarantine,
                )
                adopted_name = adoption.adopted_name
                adopted_tree = adoption.after_tree
                adopted_integrity = to_integrity_observation(adoption.final_integrity)
                outputs = _relative_files(adoption.destination_path)
            except (OSError, ContainmentSafetyError, ValueError) as error:
                incomplete.append(f"adoption-proof-unavailable:{type(error).__name__}")
            finally:
                if adoption is not None and _exists_no_follow(adoption.destination_path):
                    try:
                        _quarantine_exact(adoption.destination_path, quarantine)
                    except (OSError, ContainmentSafetyError):
                        incomplete.append("adopted-source-quarantine-failed")
        final = _capture_protected(record)
        restored = final == before
    else:
        unexpected = tuple(name for name in stage_names if name not in record.before_names)
        changed = _changed_projection_names(record)
        if unexpected or changed:
            safety.append("unexpected-staging-backup")
        quarantine_names = tuple(dict.fromkeys((*changed, *unexpected)))
        for name in quarantine_names:
            try:
                _quarantine_exact(record.stage_mods / name, quarantine)
            except (OSError, ContainmentSafetyError):
                incomplete.append("staging-quarantine-failed")
        final = _capture_protected(record)
        restored = final == before

    return _ProjectionEvidence(
        final,
        projection_count,
        targets_verified,
        production_backups,
        new_names,
        outputs,
        adopted_name,
        adopted_tree,
        adopted_integrity,
        restored,
        tuple(sorted(set(safety))),
        tuple(sorted(set(incomplete))),
    )


def _perform_recovery_cleanup(
    store: ContainmentStore,
    record: FixtureRecord,
    journal: ScenarioJournal,
) -> RecoveryCleanupEvidence:
    blockers: list[str] = []
    watch: WatchOutcome | None = None
    request_path = store.watch_path(journal.run_id, journal.scenario) / "request.json"
    if request_path.exists():
        receipt = stop_watch(request_path)
        if receipt.watch_outcome_id is None:
            blockers.append("durable-watch-outcome-unavailable")
        else:
            try:
                watch = store.load_watch_outcome(
                    journal.run_id,
                    journal.scenario,
                    receipt.watch_outcome_id,
                )
            except ContainmentStoreError:
                blockers.append("durable-watch-outcome-invalid")
            dangerous = {
                "worker-identity-uncertain",
                "worker-cleanup-refused",
                "claimed-controller-liveness-uncertain",
                "same-controller-protocol-uncertain",
            }
            receipt_reasons = set() if receipt.error is None else {
                reason for reason in dangerous if reason in receipt.error
            }
            if (
                watch is not None
                and (dangerous.intersection(watch.reason_codes) or receipt_reasons)
            ):
                blockers.append("watcher-cleanup-uncertain")
    else:
        blockers.append("watch-request-unavailable")

    process_observation = inspect_mo2_processes(record.stage_root)
    if not process_observation.complete:
        blockers.append("mo2-process-identity-uncertain")
    elif process_observation.relevant:
        blockers.append("prior-mo2-process-still-live")

    recovery_quarantine = store.quarantine_path(journal.run_id) / (
        "Recovery-" + journal.scenario.value
    )
    try:
        recovery_quarantine.mkdir(exist_ok=True)
        stage_names = _direct_names(record.stage_mods)
        changed = _changed_projection_names(record)
        unexpected = tuple(name for name in stage_names if name not in record.before_names)
        for name in dict.fromkeys((*changed, *unexpected)):
            _quarantine_exact(record.stage_mods / name, recovery_quarantine)
    except (OSError, ContainmentSafetyError):
        blockers.append("staging-quarantine-failed")

    try:
        before_normalization = _capture_protected(record)
    except (OSError, ContainmentSafetyError, ContainmentServiceError):
        before_normalization = journal.protected_before
        blockers.append("source-evidence-unavailable")
    for path in (
        record.stage_app,
        record.stage_downloads,
        record.stage_profiles,
        record.stage_overwrite,
        record.stage_cache,
        record.stage_logs,
    ):
        try:
            set_low_integrity_tree(path)
        except (OSError, RuntimeError):
            blockers.append("stage-integrity-normalization-failed")
            break
    source_integrity = _integrity_observation(record.source_root)
    stage_integrity = _integrity_observation(record.stage_root)
    if source_integrity not in {
        IntegrityObservation.MEDIUM,
        IntegrityObservation.HIGH,
        IntegrityObservation.SYSTEM,
    }:
        blockers.append("source-integrity-unverified")
    if stage_integrity is not IntegrityObservation.LOW:
        blockers.append("stage-integrity-unverified")
    projection_count, targets_verified = _projection_state(record)
    if not targets_verified:
        blockers.append("projection-integrity-unverified")
    try:
        after = _capture_protected(record)
    except (OSError, ContainmentSafetyError, ContainmentServiceError):
        after = before_normalization
        blockers.append("source-reverification-failed")
    if after != before_normalization:
        blockers.append("source-changed-during-recovery")
    return RecoveryCleanupEvidence(
        watch,
        after,
        source_integrity,
        stage_integrity,
        projection_count,
        targets_verified,
        0,
        tuple(sorted(set(blockers))),
    )


def _direct_names(root: Path) -> tuple[str, ...]:
    return tuple(sorted((entry.name for entry in os.scandir(root)), key=lambda item: (item.casefold(), item)))


def _relative_files(root: Path) -> tuple[str, ...]:
    rows: list[str] = []
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in directories:
            path = base / name
            if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise ContainmentSafetyError("adopted output contains a reparse directory")
        for name in files:
            path = base / name
            if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise ContainmentSafetyError("adopted output contains a reparse file")
            rows.append(path.relative_to(root).as_posix())
    return tuple(sorted(rows))


def _quarantine_exact(source: Path, quarantine: Path) -> None:
    metadata = source.lstat()
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise ContainmentSafetyError("quarantine source is not an exact filesystem object")
    destination = quarantine / source.name
    if destination.exists():
        raise ContainmentSafetyError("quarantine destination collision")
    os.rename(source, destination)


def _exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _integrity_observation(path: Path) -> IntegrityObservation:
    try:
        return to_integrity_observation(inspect_path_integrity(path))
    except (OSError, ValueError, RuntimeError):
        return IntegrityObservation.UNKNOWN


def _watcher_live(request_path: Path, pid: int | None) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        request_bytes = request_path.read_bytes()
        request = watch_request_from_bytes(request_bytes)
        if request_bytes != _windows_watch.watch_request_to_bytes(request):
            return False
        launch_path = request.evidence_root / LAUNCH_NAME
        launch_bytes = launch_path.read_bytes()
        launch = worker_launch_from_bytes(launch_bytes, request)
        if launch.worker_pid != pid:
            return False
        status, handle, _detail = _windows_watch._exact_process_status(
            launch.worker_pid,
            launch.worker_creation_time,
        )
    except (OSError, RuntimeError, ValueError):
        return False
    close_error = None
    if handle:
        close_error = _windows_watch._close_handle(
            handle,
            f"scenario launch watcher process {pid}",
        )
    return status == "live" and close_error is None


def _launch_key(
    store: ContainmentStore,
    run_id: str,
    scenario: ContainmentScenario,
) -> tuple[str, str, ContainmentScenario]:
    return os.path.normcase(str(store.root)), run_id, scenario


def _same_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _beneath(root: Path, candidate: Path) -> bool:
    try:
        common = os.path.commonpath((str(root), str(candidate)))
    except ValueError:
        return False
    return os.path.normcase(os.path.normpath(common)) == os.path.normcase(
        os.path.normpath(str(root))
    )


__all__ = [
    "ContainmentServiceError",
    "FixtureRecord",
    "RecoveryCleanupEvidence",
    "ScenarioEvidence",
    "adjudicate_results",
    "adjudicate_run",
    "arm_scenario",
    "capture_scenario",
    "evaluate_scenario",
    "launch_scenario",
    "prepare_run",
    "recover_scenario",
]
