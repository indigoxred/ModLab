"""Immutable records used by verified MO2 bootstrap workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from modlab.recipes.model import CheckState
from .path_budget import PathBudget


class BootstrapDisposition(StrEnum):
    CREATE = "Create"
    ADOPT = "Adopt"
    ALREADY_MANAGED = "AlreadyManaged"
    BLOCKED = "Blocked"


class BootstrapJobState(StrEnum):
    PLANNED = "Planned"
    STAGING = "Staging"
    STAGED = "Staged"
    APPLYING = "Applying"
    ACTIVATED = "Activated"
    VERIFIED = "Verified"
    RECOVERY_REQUIRED = "RecoveryRequired"
    RECOVERED = "Recovered"


class BootstrapReceiptMode(StrEnum):
    CREATED = "Created"
    ADOPTED = "Adopted"


@dataclass(frozen=True)
class FileIdentity:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class OptionalFileIdentity:
    path: str
    present: bool
    sha256: str | None
    size: int | None


@dataclass(frozen=True)
class ExtractorIdentity:
    executable: FileIdentity
    version: str


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    image_name: str
    executable_path: str


@dataclass(frozen=True)
class ProcessObservation:
    complete: bool
    relevant: tuple[ProcessIdentity, ...]
    error: str | None


@dataclass(frozen=True)
class TargetSnapshot:
    kind: str
    root: str
    inventory_sha256: str
    entry_count: int


@dataclass(frozen=True)
class ProfileSeedEvidence:
    documents_root: str
    primary_plugins: tuple[str, ...]
    skyrim_ccc: OptionalFileIdentity
    ini_sources: tuple[OptionalFileIdentity, ...]


@dataclass(frozen=True)
class BootstrapFinding:
    state: CheckState
    code: str
    message: str


@dataclass(frozen=True)
class CoverageEntry:
    key: str
    value: str


@dataclass(frozen=True)
class ArchiveEvidence:
    artifact_id: str
    metadata_sha256: str
    original_name: str
    stored_path: str
    sha256: str
    size: int
    entry_count: int
    listing_sha256: str


@dataclass(frozen=True)
class BootstrapPlan:
    schema_version: int
    plan_id: str
    disposition: BootstrapDisposition
    workspace_root: str
    steam_root: str
    game_root: str
    final_root: str
    staging_parent: str
    staging_name_template: str
    skyrim_executable: FileIdentity
    environment_sha256: str | None
    baseline_id: str | None
    baseline_status: str
    release_descriptor_path: str
    release_descriptor_sha256: str
    release_id: str
    archive: ArchiveEvidence
    extractor: ExtractorIdentity
    target: TargetSnapshot
    processes: ProcessObservation
    profile_seed: ProfileSeedEvidence
    write_templates: tuple[str, ...]
    findings: tuple[BootstrapFinding, ...]
    exclusions: tuple[str, ...]
    coverage: tuple[CoverageEntry, ...]
    downloads: tuple[str, ...]
    installations: tuple[str, ...]
    manager_changes: tuple[str, ...]
    game_changes: tuple[str, ...]
    programs_launched: tuple[str, ...]
    path_budget: PathBudget | None = None


@dataclass(frozen=True)
class BootstrapJournal:
    schema_version: int
    job_id: str
    plan_id: str
    disposition: BootstrapDisposition
    state: BootstrapJobState
    stage_root: str
    prior_root: str
    final_root: str
    prior_target_kind: str
    prior_inventory_sha256: str
    prior_entry_count: int
    stage_inventory_sha256: str | None
    stage_entry_count: int | None
    activated_inventory_sha256: str | None
    activated_entry_count: int | None
    created_at: str
    updated_at: str
    receipt_id: str | None
    error: str | None
    path_budget: PathBudget | None = None


@dataclass(frozen=True)
class BootstrapReceipt:
    schema_version: int
    receipt_id: str
    qualification: str
    mode: BootstrapReceiptMode
    plan_id: str
    job_id: str
    release_id: str
    release_descriptor_sha256: str
    archive_artifact_id: str
    archive_metadata_sha256: str
    archive_sha256: str
    archive_size: int
    skyrim_executable: FileIdentity
    mo2_executable: FileIdentity
    final_root: str
    downloads_root: str
    mods_root: str
    profiles_root: str
    overwrite_root: str
    webcache_root: str
    package_inventory_sha256: str
    package_file_count: int
    package_size: int
    mutable_package_paths: tuple[str, ...]
    extra_entries: tuple[str, ...]
    profile_state_sha256: str
    extractor: ExtractorIdentity
    verified_at: str
    findings: tuple[BootstrapFinding, ...]
    coverage: tuple[CoverageEntry, ...]
    repairable: bool
    upgrade_supported: bool


@dataclass(frozen=True)
class SetupPlanResult:
    plan: BootstrapPlan
    plan_path: Path
    paths_written: tuple[Path, ...]
    downloads: tuple[str, ...]
    installations: tuple[str, ...]
    manager_changes: tuple[str, ...]
    game_changes: tuple[str, ...]
    programs_launched: tuple[str, ...]


@dataclass(frozen=True)
class SetupApplyResult:
    outcome: str
    plan_id: str
    journal: BootstrapJournal | None
    receipt: BootstrapReceipt
    paths_written: tuple[str, ...]
    downloads: tuple[str, ...]
    installations: tuple[str, ...]
    manager_changes: tuple[str, ...]
    game_changes: tuple[str, ...]
    programs_launched: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryResult:
    outcome: str
    journal: BootstrapJournal
    receipt: BootstrapReceipt | None
    paths_written: tuple[str, ...]
    downloads: tuple[str, ...]
    installations: tuple[str, ...]
    manager_changes: tuple[str, ...]
    game_changes: tuple[str, ...]
    programs_launched: tuple[str, ...]


@dataclass(frozen=True)
class BootstrapFailureResult:
    outcome: str
    code: str
    message: str
    plan_id: str | None
    job_id: str | None
    journal: BootstrapJournal | None
    receipt: BootstrapReceipt | None
    actions_complete: bool
    paths_written: tuple[str, ...]
    downloads: tuple[str, ...]
    installations: tuple[str, ...]
    manager_changes: tuple[str, ...]
    game_changes: tuple[str, ...]
    programs_launched: tuple[str, ...]
