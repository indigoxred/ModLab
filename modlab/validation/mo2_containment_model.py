"""Immutable, portable evidence values for the MO2 containment spike."""

from dataclasses import dataclass
from enum import StrEnum


class ContainmentScenario(StrEnum):
    NEW_FOLDER = "NewFolder"
    MERGE_EXISTING = "MergeExisting"
    REPLACE_EXISTING = "ReplaceExisting"
    FOMOD_DEPENDENCY = "FomodDependency"


class ScenarioState(StrEnum):
    PREPARED = "Prepared"
    ARMED = "Armed"
    SCENARIO_STARTED = "ScenarioStarted"
    LAUNCHED = "Launched"
    CAPTURED = "Captured"
    RECOVERY_REQUIRED = "RecoveryRequired"


class ScenarioOutcome(StrEnum):
    PASSED = "Passed"
    FAILED = "Failed"
    INCOMPLETE = "Incomplete"


class WatchEvidenceCompletion(StrEnum):
    COMPLETED = "Completed"
    INCOMPLETE = "Incomplete"


class ScenarioCleanupStatus(StrEnum):
    SUCCEEDED = "Succeeded"
    REFUSED = "Refused"


class CapabilityVerdict(StrEnum):
    SUPPORTED = "Supported"
    REJECTED = "Rejected"
    INCOMPLETE = "Incomplete"


class IntegrityObservation(StrEnum):
    UNTRUSTED = "Untrusted"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    SYSTEM = "System"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class TreeIdentity:
    sha256: str
    regular_file_count: int
    directory_count: int
    total_size: int


@dataclass(frozen=True)
class WatcherEvent:
    sequence: int
    root_kind: str
    action: str
    relative_path: str


@dataclass(frozen=True)
class ProtectedState:
    source_mods: TreeIdentity
    lab_profile_sha256: str
    play_profile_sha256: str
    downloads: TreeIdentity
    overwrite: TreeIdentity
    bounded_game: TreeIdentity


@dataclass(frozen=True)
class ProcessEvidence:
    pid: int
    executable: str
    executable_version: str
    arguments: tuple[str, ...]
    working_directory: str
    integrity: IntegrityObservation


@dataclass(frozen=True)
class ScenarioJournal:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    state: ScenarioState
    source_root: str
    stage_root: str
    archive_path: str
    protected_mod_name: str
    expected_new_mod_name: str
    protected_before: ProtectedState
    monitor_pid: int | None
    mo2_pid: int | None
    error: str | None


@dataclass(frozen=True)
class WatchOutcome:
    schema_version: int
    request_id: str
    request_sha256: str
    session_id: str
    run_id: str
    scenario: ContainmentScenario
    controller_pid: int
    controller_creation_time: int
    worker_pid: int
    worker_creation_time: int
    evidence_completion: WatchEvidenceCompletion
    worker_exit_code: int | None
    ready: bool
    opened_root_kinds: tuple[str, ...]
    events: tuple[WatcherEvent, ...]
    event_bytes_sha256: str
    journal_volume_serial: int
    journal_file_id: int
    journal_byte_count: int
    journal_event_count: int
    journal_final_sequence: int
    terminal_bytes_sha256: str
    root_identities_unchanged: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioResult:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    outcome: ScenarioOutcome
    protected_before: ProtectedState
    protected_after: ProtectedState
    mo2_process: ProcessEvidence | None
    source_integrity: IntegrityObservation
    stage_integrity: IntegrityObservation
    watch_outcome_id: str
    watch_evidence_completion: WatchEvidenceCompletion
    scenario_started: bool
    fresh_retry_eligible: bool
    watcher_events: tuple[WatcherEvent, ...]
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
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioRecovery:
    schema_version: int
    run_id: str
    scenario: ContainmentScenario
    journal_id: str
    result_id: str | None
    cleanup_status: ScenarioCleanupStatus
    fresh_run_permitted: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class DecisionBindings:
    binding_version: int
    source_commit_id: str
    source_tree_id: str
    source_artifact_id: str
    protocol_version: int
    publication_policy_version: str
    fixture_version: int
    effect_receipt_version: int
    authority_policy_version: int
    mo2_version: str
    mo2_executable_sha256: str


@dataclass(frozen=True)
class CapabilityDecision:
    schema_version: int
    run_id: str
    mechanism: str
    verdict: CapabilityVerdict
    scenario_result_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    bindings: DecisionBindings | None = None

    @property
    def binding_version(self) -> int | None:
        return None if self.bindings is None else self.bindings.binding_version

    @property
    def source_commit_id(self) -> str | None:
        return None if self.bindings is None else self.bindings.source_commit_id

    @property
    def source_tree_id(self) -> str | None:
        return None if self.bindings is None else self.bindings.source_tree_id

    @property
    def source_artifact_id(self) -> str | None:
        return None if self.bindings is None else self.bindings.source_artifact_id

    @property
    def publication_policy_version(self) -> str | None:
        return None if self.bindings is None else self.bindings.publication_policy_version
