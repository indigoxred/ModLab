"""Immutable, portable evidence values for the MO2 containment spike."""
from dataclasses import dataclass
from enum import StrEnum

class ContainmentScenario(StrEnum):
    NEW_FOLDER = "NewFolder"
    MERGE_EXISTING = "MergeExisting"
    REPLACE_EXISTING = "ReplaceExisting"
    FOMOD_DEPENDENCY = "FomodDependency"
class ScenarioState(StrEnum):
    PREPARED = "Prepared"; ARMED = "Armed"; LAUNCHED = "Launched"; CAPTURED = "Captured"; RECOVERY_REQUIRED = "RecoveryRequired"
class ScenarioOutcome(StrEnum):
    PASSED = "Passed"; FAILED = "Failed"; INCOMPLETE = "Incomplete"
class CapabilityVerdict(StrEnum):
    SUPPORTED = "Supported"; REJECTED = "Rejected"; INCOMPLETE = "Incomplete"
class IntegrityObservation(StrEnum):
    UNTRUSTED = "Untrusted"; LOW = "Low"; MEDIUM = "Medium"; HIGH = "High"; SYSTEM = "System"; UNKNOWN = "Unknown"
@dataclass(frozen=True)
class TreeIdentity:
    sha256: str; regular_file_count: int; directory_count: int; total_size: int
@dataclass(frozen=True)
class WatcherEvent:
    sequence: int; root_kind: str; action: str; relative_path: str
@dataclass(frozen=True)
class ProtectedState:
    source_mods: TreeIdentity; lab_profile_sha256: str; play_profile_sha256: str; downloads: TreeIdentity; overwrite: TreeIdentity; bounded_game: TreeIdentity
@dataclass(frozen=True)
class ProcessEvidence:
    pid: int; executable: str; executable_version: str; arguments: tuple[str, ...]; working_directory: str; integrity: IntegrityObservation
@dataclass(frozen=True)
class ScenarioJournal:
    schema_version: int; run_id: str; scenario: ContainmentScenario; state: ScenarioState; source_root: str; stage_root: str; archive_path: str; protected_mod_name: str; expected_new_mod_name: str; protected_before: ProtectedState; monitor_pid: int | None; mo2_pid: int | None; error: str | None
@dataclass(frozen=True)
class ScenarioResult:
    schema_version: int; run_id: str; scenario: ContainmentScenario; outcome: ScenarioOutcome; protected_before: ProtectedState; protected_after: ProtectedState; mo2_process: ProcessEvidence | None; source_integrity: IntegrityObservation; stage_integrity: IntegrityObservation; watcher_complete: bool; watcher_events: tuple[WatcherEvent, ...]; projection_count: int; projection_targets_verified: bool; projection_payload_bytes_copied: int; production_backup_names: tuple[str, ...]; staging_new_names: tuple[str, ...]; staging_output_names: tuple[str, ...]; adopted_name: str | None; adopted_tree: TreeIdentity | None; adopted_integrity: IntegrityObservation | None; source_restored_after_quarantine: bool; reasons: tuple[str, ...]
@dataclass(frozen=True)
class CapabilityDecision:
    schema_version: int; run_id: str; mechanism: str; verdict: CapabilityVerdict; scenario_result_ids: tuple[str, ...]; reasons: tuple[str, ...]
