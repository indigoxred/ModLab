"""The shared pure Task 6 classifier and its independent observations."""
from dataclasses import dataclass
import os
from pathlib import Path
from .mo2_containment_model import (ContainmentScenario, IntegrityObservation, ProcessEvidence,
    ProtectedState, WatchOutcome, TreeIdentity, ScenarioResult, ScenarioOutcome, WatchEvidenceCompletion)
from .mo2_containment_serialization import (watch_outcome_id_for, scenario_result_to_bytes,
    deterministic_policy_violations, ContainmentFormatError)
from .mo2_containment_fixtures import (SCENARIO_ADOPTION_NAMES as _EXPECTED_NEW,
    SCENARIO_OUTPUT_FILES as _EXPECTED_OUTPUTS, REPLACEMENT_OUTPUT_FILES as _EXPECTED_REPLACEMENT_OUTPUTS)

_SCHEMA_VERSION = 1

class ScenarioEvaluationError(RuntimeError):
    """Independent observations cannot be evaluated under the scenario policy."""


def _same_path(left, right):
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(os.path.normpath(str(right)))


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
    projection_observation_complete: bool
    projection_payload_bytes_copied: int
    production_backup_names: tuple[str, ...]
    production_observation_complete: bool
    staging_new_names: tuple[str, ...]
    staging_observation_complete: bool
    staging_output_names: tuple[str, ...]
    output_observation_complete: bool
    adopted_name: str | None
    adopted_tree: TreeIdentity | None
    adopted_integrity: IntegrityObservation | None
    source_restored_after_quarantine: bool
    safety_reasons: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]


def evaluate_scenario(value: ScenarioEvidence) -> ScenarioResult:
    """Apply exact scenario policy with Failed > Incomplete > Passed precedence."""
    if not isinstance(value, ScenarioEvidence):
        raise ScenarioEvaluationError("scenario evaluation requires ScenarioEvidence")
    outcome = value.watch_outcome
    if outcome.run_id != value.run_id or outcome.scenario is not value.scenario:
        raise ScenarioEvaluationError("watch outcome is not bound to the scenario")

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
    if not value.projection_observation_complete:
        incomplete.add("projection-observation-incomplete")
    else:
        if value.projection_count <= 0:
            violations.add("projection-count-invalid")
        if not value.projection_targets_verified:
            violations.add("projection-target-changed")
    if value.projection_payload_bytes_copied != 0:
        violations.add("projection-payload-copied")
    if not value.production_observation_complete:
        incomplete.add("production-observation-incomplete")
    elif value.production_backup_names:
        violations.add("production-backup-created")
    if not value.source_restored_after_quarantine:
        violations.add("source-not-restored-after-quarantine")

    expected_name = _EXPECTED_NEW[value.scenario]
    expected_outputs = _EXPECTED_OUTPUTS.get(value.scenario, ())
    adoption = value.scenario in _EXPECTED_OUTPUTS
    if adoption:
        if not value.staging_observation_complete:
            incomplete.add("staging-observation-incomplete")
        elif value.staging_new_names != (expected_name,) and (
            value.staging_new_names or value.output_observation_complete
        ):
            violations.add("staging-new-folder-set-invalid")
        if not value.output_observation_complete:
            incomplete.add("output-observation-incomplete")
        elif value.staging_output_names != expected_outputs:
            violations.add("staging-output-set-invalid")
        if value.output_observation_complete and (
            value.adopted_name != expected_name
            or value.adopted_tree is None
            or value.adopted_integrity is not IntegrityObservation.MEDIUM
        ):
            violations.add("adoption-proof-invalid")
    elif value.scenario is ContainmentScenario.REPLACE_EXISTING:
        if not value.staging_observation_complete:
            incomplete.add("staging-observation-incomplete")
        if not value.output_observation_complete:
            incomplete.add("output-observation-incomplete")
        elif value.staging_output_names != _EXPECTED_REPLACEMENT_OUTPUTS:
            violations.add("staging-output-set-invalid")
        if (
            value.staging_new_names
            or value.adopted_name is not None
            or value.adopted_tree is not None
            or value.adopted_integrity is not None
        ):
            violations.add("unexpected-staging-output")
    elif not value.staging_observation_complete or not value.output_observation_complete:
        incomplete.add("staging-observation-incomplete")
    elif (
        value.staging_new_names
        or value.staging_output_names
        or value.adopted_name is not None
        or value.adopted_tree is not None
        or value.adopted_integrity is not None
    ):
        violations.add("unexpected-staging-output")

    probe = ScenarioResult(
        _SCHEMA_VERSION, value.run_id, value.scenario, ScenarioOutcome.INCOMPLETE,
        value.protected_before, value.protected_after, value.mo2_process,
        value.source_integrity, value.stage_integrity, watch_outcome_id_for(outcome),
        outcome.evidence_completion, value.scenario_started, False, outcome.events,
        value.projection_count, value.projection_targets_verified,
        value.projection_observation_complete,
        value.projection_payload_bytes_copied,
        tuple(sorted(set(value.production_backup_names))),
        value.production_observation_complete,
        tuple(sorted(set(value.staging_new_names))),
        value.staging_observation_complete,
        tuple(sorted(set(value.staging_output_names))),
        value.output_observation_complete,
        value.adopted_name,
        value.adopted_tree, value.adopted_integrity,
        value.source_restored_after_quarantine, ("classification-probe",),
    )
    typed_violations = set(deterministic_policy_violations(probe))
    violations.update(typed_violations)
    positive_breach = bool(outcome.events) or value.protected_before != value.protected_after
    positive_failure = positive_breach or (
        outcome.evidence_completion is WatchEvidenceCompletion.COMPLETED
        and bool(typed_violations)
    )
    if violations and positive_failure:
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
        projection_observation_complete=value.projection_observation_complete,
        projection_payload_bytes_copied=value.projection_payload_bytes_copied,
        production_backup_names=tuple(sorted(set(value.production_backup_names))),
        production_observation_complete=value.production_observation_complete,
        staging_new_names=tuple(sorted(set(value.staging_new_names))),
        staging_observation_complete=value.staging_observation_complete,
        staging_output_names=tuple(sorted(set(value.staging_output_names))),
        output_observation_complete=value.output_observation_complete,
        adopted_name=value.adopted_name,
        adopted_tree=value.adopted_tree,
        adopted_integrity=value.adopted_integrity,
        source_restored_after_quarantine=value.source_restored_after_quarantine,
        reasons=reasons,
    )
    try:
        scenario_result_to_bytes(result, outcome)
    except ContainmentFormatError as error:
        raise ScenarioEvaluationError(f"scenario evaluation is not serializable: {error}") from error
    return result
