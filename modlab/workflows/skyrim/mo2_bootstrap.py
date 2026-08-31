"""Read-only evidence planning for the verified portable MO2 bootstrap."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from modlab.adapters.mo2.archive import (
    ArchiveListing,
    CommandRunner,
    Mo2ArchiveError,
    PackageFile,
    PackageInventory,
    SubprocessCommandRunner,
    compare_package_to_existing,
    extract_and_inventory,
    free_bytes_at,
    inventory_tree,
    observe_bsdtar,
    preflight_archive,
)
from modlab.adapters.mo2.bootstrap_config import (
    Mo2BootstrapConfigError,
    classify_mo2_target,
    observe_profile_seed,
    render_modorganizer_ini,
    render_profile_files,
    write_staged_configuration,
)
from modlab.adapters.mo2.bootstrap_model import (
    ArchiveEvidence,
    BootstrapDisposition,
    BootstrapFinding,
    BootstrapJobState,
    BootstrapJournal,
    BootstrapPlan,
    BootstrapReceipt,
    BootstrapReceiptMode,
    CoverageEntry,
    ExtractorIdentity,
    FileIdentity,
    ProcessObservation,
    ProfileSeedEvidence,
    SetupApplyResult,
    SetupPlanResult,
    TargetSnapshot,
)
from modlab.adapters.mo2.bootstrap_serialization import (
    BootstrapFormatError,
    plan_id_for,
    receipt_id_for,
)
from modlab.adapters.mo2.processes import (
    ProcessInspectionError,
    inspect_mo2_processes,
    windows_documents_root,
)
from modlab.adapters.mo2.ini import Mo2IniError, parse_ini_bytes
from modlab.adapters.mo2.projection import Mo2Projection, Mo2Readiness, project_mo2_state
from modlab.adapters.mo2.release import (
    LoadedMo2Release,
    Mo2ReleaseDescriptor,
    Mo2ReleaseFormatError,
    bundled_mo2_252_path,
    load_mo2_release,
)
from modlab.adapters.mo2.scanner import inspect_skyrim_mo2
from modlab.adapters.skyrim.model import SkyrimDiscoveryReport
from modlab.adapters.skyrim.scanner import discover_skyrim_steam
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.artifacts.model import ArchiveArtifact
from modlab.artifacts.serialization import ArtifactFormatError, artifact_from_dict
from modlab.recipes.model import CheckState
from modlab.workflows.skyrim.drift import SkyrimStatusOutcome
from modlab.workflows.skyrim.mo2_bootstrap_store import (
    BootstrapReceiptMatch,
    Mo2BootstrapStore,
    Mo2BootstrapStoreError,
    StoredBootstrapJournal,
    StoredBootstrapReceipt,
    new_bootstrap_job_id,
    utc_now,
)
from modlab.workflows.skyrim.service import get_skyrim_status
from modlab.workflows.skyrim.store import (
    ConfigurationSnapshot,
    SkyrimEnvironmentNotConfiguredError,
    SkyrimEnvironmentStore,
    SkyrimEnvironmentStoreError,
)
from modlab.workspace import WorkspaceLayout, workspace_layout


class Mo2BootstrapError(RuntimeError):
    """MO2 setup evidence could not be prepared safely."""


class Mo2BootstrapRefusal(Mo2BootstrapError):
    """Planning refused a specific unsafe or incomplete input."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


BOOTSTRAP_FINDING_CODES = frozenset(
    {
        "release-unsupported",
        "release-modified",
        "artifact-missing",
        "artifact-modified",
        "artifact-release-mismatch",
        "archive-listing-unsafe",
        "archive-listing-mismatch",
        "extractor-missing",
        "extractor-redirected",
        "extractor-changed",
        "extractor-failed",
        "disk-space-insufficient",
        "skyrim-discovery-blocked",
        "environment-mismatch",
        "baseline-drifted",
        "target-redirected",
        "target-unknown-nonempty",
        "target-changed",
        "existing-package-mismatch",
        "mo2-process-running",
        "process-inspection-unknown",
        "profile-seed-changed",
        "stage-collision",
        "orphan-stage",
        "plan-invalid",
        "journal-invalid",
        "receipt-invalid",
        "recovery-required",
        "post-activation-not-ready",
    }
)

_COVERAGE = (
    CoverageEntry("installedModPayloads", "NotLinked"),
    CoverageEntry("managerArtifact", "VerifiedPackageSubset"),
    CoverageEntry("mutableManagerState", "ExcludedRecorded"),
    CoverageEntry("runtimeValidation", "NotPerformed"),
    CoverageEntry("smokeTest", "NotPerformed"),
)
_EXCLUSIONS = ("installedModPayloads", "runtimeValidation", "smokeTest")
_WRITE_TEMPLATES = (
    "games/skyrim-se-ae/tool-installations/mo2/{receiptId}.json",
    "runtime/jobs/mo2-bootstrap/plans/{planId}.json",
    "runtime/jobs/mo2-bootstrap/{jobId}/journal.json",
)
_JOB_DIRECTORY = re.compile(r"^[0-9a-f]{32}$")
_STAGE_DIRECTORY = re.compile(r"^\.skyrim-se-ae\.modlab-stage-([0-9a-f]{32})$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class _ArtifactObservation:
    record: ArchiveArtifact
    metadata_sha256: str
    metadata_data: bytes
    payload_path: Path


@dataclass(frozen=True)
class _EnvironmentSelection:
    steam_root: Path
    snapshot: ConfigurationSnapshot | None
    baseline_id: str | None
    baseline_status: str


@dataclass(frozen=True)
class _ExistingObservation:
    projection: Mo2Projection
    executable: FileIdentity
    package_inventory: PackageInventory | None
    extra_entries: tuple[str, ...] | None


@dataclass(frozen=True)
class _CollectedSetup:
    plan: BootstrapPlan
    layout: WorkspaceLayout
    release: LoadedMo2Release
    artifact: _ArtifactObservation
    listing: ArchiveListing
    existing: _ExistingObservation | None
    compatible_receipt: StoredBootstrapReceipt | None


@dataclass(frozen=True)
class _ManagerSnapshot:
    inventory: PackageInventory
    entries: tuple["_EntryIdentity", ...]


@dataclass(frozen=True)
class _EntryIdentity:
    relative_path: str
    kind: str
    device: int
    inode: int
    mode_type: int
    file_attributes: int
    size: int | None
    modified_ns: int | None
    changed_ns: int | None


def prepare_mo2_setup(
    *,
    artifact_id: str,
    workspace_root: Path,
    steam_root: Path | None,
    release_path: Path = bundled_mo2_252_path(),
    extractor_path: Path = Path(r"C:\Windows\System32\tar.exe"),
    documents_root: Path | None = None,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    process_inspector: Callable[[Path], ProcessObservation] = inspect_mo2_processes,
    command_runner: CommandRunner | None = None,
    free_space_reader: Callable[[Path], int] = free_bytes_at,
) -> SetupPlanResult:
    """Collect stable evidence and retain one immutable, side-effect-free plan."""
    collected = _collect_mo2_setup(
        artifact_id=artifact_id,
        workspace_root=workspace_root,
        steam_root=steam_root,
        release_path=release_path,
        extractor_path=extractor_path,
        documents_root=documents_root,
        version_reader=version_reader,
        process_inspector=process_inspector,
        command_runner=command_runner,
        free_space_reader=free_space_reader,
    )
    try:
        stored = Mo2BootstrapStore(collected.layout.root).write_plan(collected.plan)
    except (BootstrapFormatError, Mo2BootstrapStoreError, OSError) as error:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"bootstrap plan could not be retained safely: {error}"
        ) from error
    written = (stored.path,) if stored.changed else ()
    return SetupPlanResult(
        plan=stored.plan,
        plan_path=stored.path,
        paths_written=written,
        downloads=(),
        installations=(),
        manager_changes=(),
        game_changes=(),
        programs_launched=stored.plan.programs_launched,
    )


def _collect_mo2_setup(
    *,
    artifact_id: str,
    workspace_root: Path,
    steam_root: Path | None,
    release_path: Path,
    extractor_path: Path,
    documents_root: Path | None,
    version_reader: Callable[[Path], str | None],
    process_inspector: Callable[[Path], ProcessObservation],
    command_runner: CommandRunner | None,
    free_space_reader: Callable[[Path], int],
) -> _CollectedSetup:
    """Collect stable bootstrap evidence without retaining any new state."""
    requested_workspace = Path(workspace_root).expanduser().absolute()
    _require_direct_workspace(requested_workspace)
    layout = workspace_layout(requested_workspace)
    if layout.root != requested_workspace:
        raise Mo2BootstrapRefusal(
            "plan-invalid",
            f"workspace root is redirected: {requested_workspace}",
        )
    _require_direct_workspace_branches(layout)

    environment = _select_environment(layout, steam_root, version_reader)
    findings: dict[str, BootstrapFinding] = {}

    release = _load_release(release_path)
    release_reference = _release_descriptor_reference(release.path)
    _put_finding(
        findings,
        CheckState.PASSED,
        "release-unsupported",
        f"Curated release {release.descriptor.release_id} is supported.",
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "release-modified",
        "The curated release descriptor remained byte-stable.",
    )

    artifact = _observe_artifact(layout, artifact_id)
    _require_release_artifact(release.descriptor, artifact.record)
    _put_finding(
        findings,
        CheckState.PASSED,
        "artifact-missing",
        "The retained MO2 archive is present.",
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "artifact-modified",
        "The retained MO2 archive matches its content address.",
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "artifact-release-mismatch",
        "The retained archive matches the curated release identity.",
    )

    runner = command_runner or SubprocessCommandRunner()
    extractor = _observe_extractor(extractor_path, runner)
    listing = _observe_archive_listing(
        artifact.payload_path,
        release.descriptor,
        Path(extractor.executable.path),
        runner,
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "archive-listing-unsafe",
        "The archive contains only safe regular files and directories.",
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "archive-listing-mismatch",
        "The archive listing matches the curated release.",
    )

    discovery = _discover_ready_skyrim(environment.steam_root, version_reader)
    game_root = Path(discovery.game_root or "")
    game_executable = _game_executable_identity(discovery, game_root)
    _require_environment_game_match(environment.snapshot, discovery)
    _put_finding(
        findings,
        CheckState.PASSED,
        "skyrim-discovery-blocked",
        "Steam app 489830 and SkyrimSE.exe were observed as Ready.",
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "environment-mismatch",
        "The selected Steam root agrees with retained Skyrim intent.",
    )

    document_root = _select_documents_root(documents_root)
    seed = _observe_seed(game_root, document_root)
    _put_finding(
        findings,
        CheckState.PASSED,
        "profile-seed-changed",
        "Primary plug-ins and the three INI sources remained stable.",
    )

    target = classify_mo2_target(layout)
    _classify_target_finding(layout, target, findings)
    processes = _observe_processes(process_inspector, layout.skyrim_mo2, findings)
    _observe_recovery_state(layout, findings)

    existing: _ExistingObservation | None = None
    if target.kind == "Existing":
        existing = _observe_existing_manager(
            layout,
            game_root,
            release.descriptor,
            listing,
            version_reader,
            findings,
        )

    environment = replace(
        environment,
        baseline_status=_observe_baseline(
            environment,
            layout,
            version_reader,
            findings,
        ),
    )

    archive_evidence = ArchiveEvidence(
        artifact_id=artifact.record.artifact_id,
        metadata_sha256=artifact.metadata_sha256,
        original_name=artifact.record.original_name,
        stored_path=artifact.record.stored_relative_path,
        sha256=artifact.record.sha256,
        size=artifact.record.size,
        entry_count=listing.entry_count,
        listing_sha256=listing.canonical_sha256,
    )
    compatible_receipt = _find_compatible_receipt(
        layout,
        release,
        archive_evidence,
        game_executable,
        extractor,
        existing,
        findings,
    )
    receipt_invalid_finding = findings.get("receipt-invalid")

    available_free_bytes = (
        0
        if compatible_receipt is not None
        else _read_free_space(free_space_reader, layout.skyrim_mo2.parent)
    )
    _record_free_space(
        available_free_bytes,
        release.descriptor.minimum_free_bytes,
        staging_required=compatible_receipt is None,
        findings=findings,
    )

    _require_stable_inputs(
        layout=layout,
        environment=environment,
        release=release,
        artifact=artifact,
        discovery=discovery,
        target=target,
        seed=seed,
        extractor=extractor,
        listing=listing,
        existing=existing,
        compatible_receipt=compatible_receipt,
        receipt_invalid_finding=receipt_invalid_finding,
        archive_evidence=archive_evidence,
        game_executable=game_executable,
        documents_root=document_root,
        version_reader=version_reader,
    )

    disposition = _disposition_for(
        target,
        findings.values(),
        compatible_receipt,
    )
    programs = (
        f"{extractor.executable.path} [version]",
        f"{extractor.executable.path} [list-names]",
        f"{extractor.executable.path} [list-types]",
    )
    plan = BootstrapPlan(
        schema_version=1,
        plan_id="bootstrap-plan-sha256:" + "0" * 64,
        disposition=disposition,
        workspace_root=str(layout.root),
        steam_root=discovery.steam_root,
        game_root=str(game_root),
        final_root=str(layout.skyrim_mo2),
        staging_parent=str(layout.skyrim_mo2.parent),
        staging_name_template=".skyrim-se-ae.modlab-stage-<job-hex>",
        skyrim_executable=game_executable,
        environment_sha256=(
            None if environment.snapshot is None else environment.snapshot.sha256
        ),
        baseline_id=environment.baseline_id,
        baseline_status=environment.baseline_status,
        release_descriptor_path=release_reference,
        release_descriptor_sha256=release.sha256,
        release_id=release.descriptor.release_id,
        archive=archive_evidence,
        extractor=extractor,
        target=target,
        processes=processes,
        profile_seed=seed,
        write_templates=_WRITE_TEMPLATES,
        findings=_sorted_findings(findings.values()),
        exclusions=_EXCLUSIONS,
        coverage=_COVERAGE,
        downloads=(),
        installations=(),
        manager_changes=(),
        game_changes=(),
        programs_launched=programs,
    )
    try:
        plan = replace(plan, plan_id=plan_id_for(plan))
    except BootstrapFormatError as error:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"bootstrap plan could not be derived safely: {error}"
        ) from error
    return _CollectedSetup(
        plan=plan,
        layout=layout,
        release=release,
        artifact=artifact,
        listing=listing,
        existing=existing,
        compatible_receipt=compatible_receipt,
    )


def apply_mo2_setup(
    plan_id: str,
    workspace_root: Path,
    *,
    release_path: Path = bundled_mo2_252_path(),
    extractor_path: Path = Path(r"C:\Windows\System32\tar.exe"),
    documents_root: Path | None = None,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    process_inspector: Callable[[Path], ProcessObservation] = inspect_mo2_processes,
    command_runner: CommandRunner | None = None,
    free_space_reader: Callable[[Path], int] = free_bytes_at,
    clock: Callable[[], datetime] = utc_now,
    job_id_factory: Callable[[], str] = new_bootstrap_job_id,
) -> SetupApplyResult:
    """Revalidate and apply an immutable MO2 setup plan.

    Task 8 implements only the zero-manager-write Adopt and AlreadyManaged
    branches. The transactional Create branch is added by Task 9 without
    changing this public signature.
    """
    requested_workspace = Path(workspace_root).expanduser().absolute()
    try:
        _require_direct_workspace(requested_workspace)
        layout = workspace_layout(requested_workspace)
        _require_direct_workspace_branches(layout)
        store = Mo2BootstrapStore(
            layout.root,
            clock=clock,
            job_id_factory=job_id_factory,
        )
        stored_plan = store.load_plan(plan_id)
    except (Mo2BootstrapStoreError, OSError) as error:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"stored bootstrap plan is unavailable or invalid: {error}"
        ) from error

    plan = stored_plan.plan
    if plan.disposition is BootstrapDisposition.BLOCKED:
        raise Mo2BootstrapRefusal(
            "plan-invalid", "Blocked bootstrap plans cannot be applied"
        )
    if plan.disposition not in {
        BootstrapDisposition.CREATE,
        BootstrapDisposition.ADOPT,
        BootstrapDisposition.ALREADY_MANAGED,
    }:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"unsupported bootstrap disposition: {plan.disposition}"
        )

    collected = _collect_mo2_setup(
        artifact_id=plan.archive.artifact_id,
        workspace_root=layout.root,
        steam_root=Path(plan.steam_root),
        release_path=release_path,
        extractor_path=extractor_path,
        documents_root=documents_root,
        version_reader=version_reader,
        process_inspector=process_inspector,
        command_runner=command_runner,
        free_space_reader=free_space_reader,
    )
    _require_apply_revalidation(plan, collected.plan)
    _require_stopped_processes(collected.plan.processes)
    latest_processes = _observe_processes(process_inspector, layout.skyrim_mo2, {})
    _require_stopped_processes(latest_processes)

    if plan.disposition is BootstrapDisposition.CREATE:
        if collected.plan.disposition is not BootstrapDisposition.CREATE:
            raise Mo2BootstrapRefusal(
                "target-changed",
                "the current target no longer qualifies for safe creation",
            )
        return _apply_create_plan(
            plan=plan,
            collected=collected,
            store=store,
            process_inspector=process_inspector,
            command_runner=command_runner,
            free_space_reader=free_space_reader,
            clock=clock,
            version_reader=version_reader,
        )

    if collected.compatible_receipt is not None:
        return SetupApplyResult(
            outcome=BootstrapDisposition.ALREADY_MANAGED.value,
            plan_id=plan.plan_id,
            journal=None,
            receipt=collected.compatible_receipt.receipt,
            paths_written=(),
            downloads=(),
            installations=(),
            manager_changes=(),
            game_changes=(),
            programs_launched=collected.plan.programs_launched,
        )
    if plan.disposition is BootstrapDisposition.ALREADY_MANAGED:
        raise Mo2BootstrapRefusal(
            "receipt-invalid",
            "the plan's verified bootstrap receipt is no longer compatible",
        )
    if collected.plan.disposition is not BootstrapDisposition.ADOPT:
        raise Mo2BootstrapRefusal(
            "target-changed",
            "the current target no longer qualifies for safe adoption",
        )
    return _apply_adopt_plan(
        plan=plan,
        collected=collected,
        store=store,
        process_inspector=process_inspector,
        command_runner=command_runner,
        free_space_reader=free_space_reader,
        clock=clock,
        version_reader=version_reader,
    )


def _require_apply_revalidation(
    planned: BootstrapPlan,
    current: BootstrapPlan,
) -> None:
    if not current.processes.complete or current.processes.error is not None:
        raise Mo2BootstrapRefusal(
            "process-inspection-unknown",
            "current MO2 process inspection is incomplete",
        )
    if current.processes.relevant:
        raise Mo2BootstrapRefusal(
            "mo2-process-running",
            "a relevant MO2 process is still running",
        )
    if planned.processes.complete is not True or planned.processes.error is not None:
        raise Mo2BootstrapRefusal(
            "plan-invalid", "stored plan contains incomplete process evidence"
        )
    if planned.processes.relevant and current.processes.relevant:
        raise Mo2BootstrapRefusal(
            "mo2-process-running", "a relevant MO2 process is still running"
        )

    allowed_dispositions = {planned.disposition}
    if planned.disposition is BootstrapDisposition.ADOPT:
        allowed_dispositions.add(BootstrapDisposition.ALREADY_MANAGED)
    if current.disposition not in allowed_dispositions:
        raise Mo2BootstrapRefusal(
            "target-changed",
            "bootstrap disposition changed outside the allowed receipt transition",
        )

    ignored = {"plan_id", "disposition", "processes", "findings"}
    for field in fields(BootstrapPlan):
        if field.name in ignored:
            continue
        if getattr(planned, field.name) != getattr(current, field.name):
            raise Mo2BootstrapRefusal(
                _revalidation_code_for(field.name),
                f"bootstrap evidence changed after planning: {field.name}",
            )

    allowed_finding_changes = {"mo2-process-running"}
    if current.disposition is BootstrapDisposition.ALREADY_MANAGED:
        allowed_finding_changes.add("disk-space-insufficient")
    planned_findings = {
        item.code: item
        for item in planned.findings
        if item.code not in allowed_finding_changes
    }
    current_findings = {
        item.code: item
        for item in current.findings
        if item.code not in allowed_finding_changes
    }
    if planned_findings != current_findings:
        raise Mo2BootstrapRefusal(
            "target-changed", "bootstrap findings changed after planning"
        )


def _revalidation_code_for(field_name: str) -> str:
    return {
        "release_descriptor_path": "release-modified",
        "release_descriptor_sha256": "release-modified",
        "release_id": "release-unsupported",
        "archive": "artifact-modified",
        "extractor": "extractor-changed",
        "skyrim_executable": "skyrim-discovery-blocked",
        "steam_root": "environment-mismatch",
        "game_root": "environment-mismatch",
        "environment_sha256": "environment-mismatch",
        "baseline_id": "baseline-drifted",
        "baseline_status": "baseline-drifted",
        "profile_seed": "profile-seed-changed",
        "target": "target-changed",
    }.get(field_name, "plan-invalid")


def _require_stopped_processes(observation: ProcessObservation) -> None:
    if not observation.complete or observation.error is not None:
        raise Mo2BootstrapRefusal(
            "process-inspection-unknown", "MO2 process inspection is incomplete"
        )
    if observation.relevant:
        raise Mo2BootstrapRefusal(
            "mo2-process-running", "a relevant MO2 process is still running"
        )


def _require_extraction_inputs_current(collected: _CollectedSetup) -> None:
    repeated_release = _load_release(collected.release.path)
    if repeated_release != collected.release:
        raise Mo2BootstrapRefusal(
            "release-modified", "release descriptor changed at extraction boundary"
        )
    repeated_artifact = _observe_artifact(
        collected.layout,
        collected.artifact.record.artifact_id,
    )
    if repeated_artifact != collected.artifact:
        raise Mo2BootstrapRefusal(
            "artifact-modified", "retained archive changed at extraction boundary"
        )
    try:
        sha256, size = _stable_direct_file_hash(
            Path(collected.plan.extractor.executable.path),
            "system bsdtar",
        )
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "extractor-changed", f"system bsdtar changed at extraction boundary: {error}"
        ) from error
    if (
        sha256 != collected.plan.extractor.executable.sha256
        or size != collected.plan.extractor.executable.size
    ):
        raise Mo2BootstrapRefusal(
            "extractor-changed", "system bsdtar changed at extraction boundary"
        )


def _apply_create_plan(
    *,
    plan: BootstrapPlan,
    collected: _CollectedSetup,
    store: Mo2BootstrapStore,
    process_inspector: Callable[[Path], ProcessObservation],
    command_runner: CommandRunner | None,
    free_space_reader: Callable[[Path], int],
    clock: Callable[[], datetime],
    version_reader: Callable[[Path], str | None],
) -> SetupApplyResult:
    runner = command_runner or SubprocessCommandRunner()
    try:
        job = store.create_job(plan)
    except (Mo2BootstrapStoreError, OSError) as error:
        raise Mo2BootstrapRefusal(
            "journal-invalid", f"bootstrap creation job could not be created: {error}"
        ) from error

    journal_path = job.path.relative_to(collected.layout.root).as_posix()
    package: PackageInventory | None = None
    stage_snapshot: _ManagerSnapshot | None = None
    stage_identity: _EntryIdentity | None = None
    prior_snapshot: _ManagerSnapshot | None = None
    prior_identity: _EntryIdentity | None = None
    prior_present = False
    prior_moved = False
    activated = False
    manager_ancestry: tuple[tuple[Path, _EntryIdentity], ...] | None = None
    prior_parent_ancestry: tuple[tuple[Path, _EntryIdentity], ...] | None = None
    try:
        manager_ancestry = _capture_cleanup_ancestry(
            collected.layout.skyrim_mo2.parent
        )
        prior_parent_ancestry = _capture_cleanup_ancestry(
            Path(job.journal.prior_root).parent
        )
        _require_stopped_processes(
            _observe_processes(process_inspector, collected.layout.skyrim_mo2, {})
        )
        job = _transition_create_job(
            store,
            job,
            BootstrapJobState.PLANNED,
            BootstrapJobState.STAGING,
            clock,
        )
        _require_extraction_inputs_current(collected)
        stage_root = Path(job.journal.stage_root)
        try:
            stage_root.mkdir()
        except OSError as error:
            raise Mo2ArchiveError(
                f"cannot create exact bootstrap staging root: {error}"
            ) from error
        stage_identity = _direct_directory_identity(stage_root, "staging root")
        package = extract_and_inventory(
            collected.artifact.payload_path,
            collected.release.descriptor,
            Path(collected.plan.extractor.executable.path),
            stage_root / "app",
            runner=runner,
            free_space_reader=free_space_reader,
        )
        _require_extraction_inputs_current(collected)
        _create_staged_empty_directories(stage_root)
        manager_ini = render_modorganizer_ini(
            collected.layout,
            Path(plan.game_root),
            collected.release.descriptor,
        )
        profile_files = render_profile_files(plan.profile_seed)
        configuration_inventory = write_staged_configuration(
            stage_root,
            manager_ini,
            profile_files,
        )
        stage_snapshot = _validate_staged_create(
            stage_root=stage_root,
            package=package,
            configuration=configuration_inventory,
            manager_ini=manager_ini,
            profile_files=profile_files,
            release=collected.release.descriptor,
            listing=collected.listing,
        )
        if _observe_seed(Path(plan.game_root), Path(plan.profile_seed.documents_root)) != plan.profile_seed:
            raise Mo2BootstrapRefusal(
                "profile-seed-changed",
                "profile seed changed after staged configuration was assembled",
            )
        if classify_mo2_target(collected.layout) != plan.target:
            raise Mo2BootstrapRefusal(
                "target-changed", "MO2 target changed before staged activation"
            )
        _require_stopped_processes(
            _observe_processes(process_inspector, collected.layout.skyrim_mo2, {})
        )
        job = _transition_create_job(
            store,
            job,
            BootstrapJobState.STAGING,
            BootstrapJobState.STAGED,
            clock,
            stage_inventory_sha256=stage_snapshot.inventory.sha256,
            stage_entry_count=stage_snapshot.inventory.file_count,
        )

        final_root = collected.layout.skyrim_mo2
        prior_root = Path(job.journal.prior_root)
        prior_metadata = _lstat_if_exists(final_root)
        prior_present = prior_metadata is not None
        if prior_present:
            prior_identity = _direct_directory_identity(final_root, "empty MO2 target")
            prior_snapshot = _snapshot_manager_tree(final_root)
        if _lstat_if_exists(prior_root) is not None:
            raise Mo2BootstrapRefusal(
                "target-changed", "bootstrap prior path appeared before activation"
            )
        if classify_mo2_target(collected.layout) != plan.target:
            raise Mo2BootstrapRefusal(
                "target-changed", "MO2 target changed at the activation boundary"
            )
        job = _transition_create_job(
            store,
            job,
            BootstrapJobState.STAGED,
            BootstrapJobState.APPLYING,
            clock,
        )
        _require_stopped_processes(
            _observe_processes(process_inspector, collected.layout.skyrim_mo2, {})
        )

        if prior_present:
            assert prior_snapshot is not None and prior_identity is not None
            assert manager_ancestry is not None and prior_parent_ancestry is not None
            _validate_captured_ancestry(manager_ancestry)
            _validate_captured_ancestry(prior_parent_ancestry)
            if _lstat_if_exists(prior_root) is not None:
                raise Mo2BootstrapRefusal(
                    "target-changed", "bootstrap prior path appeared before preservation"
                )
            _require_tree_identity(final_root, prior_identity, prior_snapshot, "empty target")
            _validate_captured_ancestry(manager_ancestry)
            _validate_captured_ancestry(prior_parent_ancestry)
            if _lstat_if_exists(prior_root) is not None:
                raise Mo2BootstrapRefusal(
                    "target-changed",
                    "bootstrap prior path appeared immediately before preservation",
                )
            _replace_path(final_root, prior_root)
            prior_moved = True
            _validate_captured_ancestry(manager_ancestry)
            _validate_captured_ancestry(prior_parent_ancestry)
            if _lstat_if_exists(final_root) is not None:
                raise Mo2BootstrapRefusal(
                    "target-changed", "final target remained after prior preservation"
                )
            _require_tree_identity(prior_root, prior_identity, prior_snapshot, "preserved target")
        elif _lstat_if_exists(final_root) is not None:
            raise Mo2BootstrapRefusal(
                "target-changed", "absent MO2 target appeared during activation"
            )

        assert stage_snapshot is not None and stage_identity is not None
        assert manager_ancestry is not None
        _validate_captured_ancestry(manager_ancestry)
        if prior_moved:
            assert prior_parent_ancestry is not None
            _validate_captured_ancestry(prior_parent_ancestry)
            assert prior_snapshot is not None and prior_identity is not None
            _require_tree_identity(
                prior_root,
                prior_identity,
                prior_snapshot,
                "preserved target",
            )
        _require_tree_identity(stage_root, stage_identity, stage_snapshot, "staged instance")
        _validate_captured_ancestry(manager_ancestry)
        if prior_moved:
            assert prior_parent_ancestry is not None
            _validate_captured_ancestry(prior_parent_ancestry)
        if _lstat_if_exists(final_root) is not None:
            raise Mo2BootstrapRefusal(
                "target-changed", "final target appeared immediately before activation"
            )
        _replace_path(stage_root, final_root)
        activated = True
        _validate_captured_ancestry(manager_ancestry)
        if prior_moved:
            assert prior_parent_ancestry is not None
            _validate_captured_ancestry(prior_parent_ancestry)
        _require_tree_identity(final_root, stage_identity, stage_snapshot, "activated instance")
        job = _transition_create_job(
            store,
            job,
            BootstrapJobState.APPLYING,
            BootstrapJobState.ACTIVATED,
            clock,
            activated_inventory_sha256=stage_snapshot.inventory.sha256,
            activated_entry_count=stage_snapshot.inventory.file_count,
        )

        existing = _verify_created_instance(
            collected=collected,
            plan=plan,
            package=package,
            stage_snapshot=stage_snapshot,
            stage_identity=stage_identity,
            version_reader=version_reader,
        )
        receipt = _created_receipt(
            plan=plan,
            job=job.journal,
            collected=collected,
            existing=existing,
            package=package,
            clock=clock,
        )
        stored_receipt = store.write_receipt(receipt)
        _require_stopped_processes(
            _observe_processes(process_inspector, collected.layout.skyrim_mo2, {})
        )
        _require_tree_identity(
            final_root,
            stage_identity,
            stage_snapshot,
            "activated instance",
        )
        job = _transition_create_job(
            store,
            job,
            BootstrapJobState.ACTIVATED,
            BootstrapJobState.VERIFIED,
            clock,
            receipt_id=stored_receipt.receipt.receipt_id,
        )
    except Mo2BootstrapRefusal as error:
        rollback_error = _restore_create_prior(
            collected.layout.skyrim_mo2,
            Path(job.journal.prior_root),
            prior_present=prior_present,
            prior_moved=prior_moved,
            activated=activated,
            prior_identity=prior_identity,
            prior_snapshot=prior_snapshot,
            manager_ancestry=manager_ancestry,
            prior_parent_ancestry=prior_parent_ancestry,
        )
        _mark_create_recovery(store, job, error, clock, rollback_error)
        if rollback_error is not None:
            raise Mo2BootstrapRefusal(
                "recovery-required",
                f"MO2 creation failed and prior restoration also failed: {rollback_error}",
            ) from error
        raise
    except (
        BootstrapFormatError,
        Mo2ArchiveError,
        Mo2BootstrapConfigError,
        Mo2BootstrapStoreError,
        Mo2IniError,
        OSError,
    ) as error:
        rollback_error = _restore_create_prior(
            collected.layout.skyrim_mo2,
            Path(job.journal.prior_root),
            prior_present=prior_present,
            prior_moved=prior_moved,
            activated=activated,
            prior_identity=prior_identity,
            prior_snapshot=prior_snapshot,
            manager_ancestry=manager_ancestry,
            prior_parent_ancestry=prior_parent_ancestry,
        )
        _mark_create_recovery(store, job, error, clock, rollback_error)
        detail = f"MO2 creation requires recovery: {error}"
        if rollback_error is not None:
            detail += f"; prior restoration failed: {rollback_error}"
        raise Mo2BootstrapRefusal("recovery-required", detail) from error
    except Exception as error:
        rollback_error = _restore_create_prior(
            collected.layout.skyrim_mo2,
            Path(job.journal.prior_root),
            prior_present=prior_present,
            prior_moved=prior_moved,
            activated=activated,
            prior_identity=prior_identity,
            prior_snapshot=prior_snapshot,
            manager_ancestry=manager_ancestry,
            prior_parent_ancestry=prior_parent_ancestry,
        )
        _mark_create_recovery(store, job, error, clock, rollback_error)
        detail = f"MO2 creation failed safely: {error}"
        if rollback_error is not None:
            detail += f"; prior restoration failed: {rollback_error}"
        raise Mo2BootstrapRefusal("recovery-required", detail) from error

    if prior_moved:
        assert prior_snapshot is not None and prior_identity is not None
        try:
            _remove_preserved_empty_prior(
                Path(job.journal.prior_root),
                prior_identity,
                prior_snapshot,
            )
        except (Mo2ArchiveError, OSError) as error:
            raise Mo2BootstrapRefusal(
                "recovery-required",
                f"creation verified but preserved empty target was not cleaned: {error}",
            ) from error

    programs = (
        *collected.plan.programs_launched,
        f"{plan.extractor.executable.path} [extract]",
    )
    return SetupApplyResult(
        outcome=BootstrapReceiptMode.CREATED.value,
        plan_id=plan.plan_id,
        journal=job.journal,
        receipt=stored_receipt.receipt,
        paths_written=tuple(
            sorted(
                (
                    journal_path,
                    stored_receipt.path.relative_to(
                        collected.layout.root
                    ).as_posix(),
                ),
                key=str.casefold,
            )
        ),
        downloads=(),
        installations=("portable-mo2-create",),
        manager_changes=tuple(
            f"{collected.layout.skyrim_mo2.relative_to(collected.layout.root).as_posix()}/"
            f"{item.relative_path}"
            for item in stage_snapshot.inventory.files
        ),
        game_changes=(),
        programs_launched=programs,
    )


def _transition_create_job(
    store: Mo2BootstrapStore,
    observed: StoredBootstrapJournal,
    expected: BootstrapJobState,
    target: BootstrapJobState,
    clock: Callable[[], datetime],
    **changes: object,
) -> StoredBootstrapJournal:
    replacement = replace(
        observed.journal,
        state=target,
        updated_at=_bootstrap_timestamp(clock()),
        error=None,
        **changes,
    )
    return store.transition_job(observed, expected, replacement)


def _create_staged_empty_directories(stage_root: Path) -> None:
    for name in ("downloads", "mods", "overwrite", "webcache"):
        target = stage_root / name
        try:
            target.mkdir()
        except OSError as error:
            raise Mo2BootstrapConfigError(
                f"could not create exact staged {name} directory: {error}"
            ) from error
        _direct_directory_identity(target, f"staged {name} directory")


def _validate_staged_create(
    *,
    stage_root: Path,
    package: PackageInventory,
    configuration: PackageInventory,
    manager_ini: bytes,
    profile_files: dict[PurePosixPath, bytes],
    release: Mo2ReleaseDescriptor,
    listing: ArchiveListing,
) -> _ManagerSnapshot:
    app_inventory = inventory_tree(stage_root / "app")
    app_comparison = compare_package_to_existing(package, app_inventory, release)
    if not app_comparison.compatible or app_comparison.allowed_extras != (
        "ModOrganizer.ini",
    ):
        raise Mo2BootstrapConfigError(
            "staged app does not contain the exact package plus ModOrganizer.ini"
        )
    expected_files = tuple(
        PackageFile(
            relative_path=f"app/{item.relative_path}",
            sha256=item.sha256,
            size=item.size,
        )
        for item in package.files
    ) + configuration.files
    expected_inventory = _inventory_from_files(expected_files)
    snapshot = _snapshot_manager_tree(stage_root)
    if snapshot.inventory != expected_inventory:
        raise Mo2BootstrapConfigError(
            "staged instance inventory differs from its exact package and seed"
        )

    expected_directories = {"app", "downloads", "mods", "overwrite", "profiles", "webcache"}
    for item in expected_inventory.files:
        path = PurePosixPath(item.relative_path)
        for depth in range(1, len(path.parts)):
            expected_directories.add(PurePosixPath(*path.parts[:depth]).as_posix())
    for entry in listing.entries:
        if entry.kind == "directory":
            path = PurePosixPath("app") / entry.relative_path
            for depth in range(1, len(path.parts) + 1):
                expected_directories.add(PurePosixPath(*path.parts[:depth]).as_posix())
    observed_directories = {
        entry.relative_path for entry in snapshot.entries if entry.kind == "directory"
    }
    if observed_directories != expected_directories:
        raise Mo2BootstrapConfigError(
            "staged instance directory set differs from the exact contained layout"
        )
    observed_manager_ini = (stage_root / "app" / "ModOrganizer.ini").read_bytes()
    if observed_manager_ini != manager_ini:
        raise Mo2BootstrapConfigError("staged ModOrganizer.ini bytes changed")
    parse_ini_bytes(observed_manager_ini)
    for relative, data in profile_files.items():
        observed = stage_root.joinpath(*relative.parts).read_bytes()
        if observed != data:
            raise Mo2BootstrapConfigError(
                f"staged profile file changed: {relative.as_posix()}"
            )
        if relative.name == "settings.ini":
            parse_ini_bytes(observed)
    return snapshot


def _inventory_from_files(files) -> PackageInventory:
    ordered = tuple(
        sorted(files, key=lambda item: (item.relative_path.casefold(), item.relative_path))
    )
    if len({item.relative_path.casefold() for item in ordered}) != len(ordered):
        raise Mo2BootstrapConfigError("staged inventory contains a duplicate path")
    canonical = json.dumps(
        [[item.relative_path, item.sha256, item.size] for item in ordered],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return PackageInventory(
        files=ordered,
        total_size=sum(item.size for item in ordered),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _direct_directory_identity(path: Path, label: str) -> _EntryIdentity:
    metadata = _direct_cleanup_metadata(path, label)
    if not stat.S_ISDIR(metadata.st_mode):
        raise Mo2ArchiveError(f"{label} is not a direct directory: {path}")
    return _entry_identity(".", "directory", metadata)


def _require_tree_identity(
    root: Path,
    expected_root: _EntryIdentity,
    expected_tree: _ManagerSnapshot,
    label: str,
) -> None:
    if _direct_directory_identity(root, label) != expected_root:
        raise Mo2ArchiveError(f"{label} root identity changed: {root}")
    if _snapshot_manager_tree(root) != expected_tree:
        raise Mo2ArchiveError(f"{label} tree changed: {root}")


def _verify_created_instance(
    *,
    collected: _CollectedSetup,
    plan: BootstrapPlan,
    package: PackageInventory,
    stage_snapshot: _ManagerSnapshot,
    stage_identity: _EntryIdentity,
    version_reader: Callable[[Path], str | None],
) -> _ExistingObservation:
    _require_tree_identity(
        collected.layout.skyrim_mo2,
        stage_identity,
        stage_snapshot,
        "activated instance",
    )
    findings: dict[str, BootstrapFinding] = {}
    existing = _observe_existing_manager(
        collected.layout,
        Path(plan.game_root),
        collected.release.descriptor,
        collected.listing,
        version_reader,
        findings,
    )
    blocked = tuple(
        finding for finding in findings.values() if finding.state is CheckState.BLOCKED
    )
    if (
        existing is None
        or blocked
        or existing.projection.readiness is not Mo2Readiness.READY
        or existing.package_inventory != package
        or existing.extra_entries != ("ModOrganizer.ini",)
        or existing.projection.adapter_state_sha256 is None
    ):
        raise Mo2BootstrapRefusal(
            "post-activation-not-ready",
            "activated MO2 does not have complete, exact Ready package evidence",
        )
    state = existing.projection.adapter_state
    if (
        state.installed_mods
        or state.overwrite_entries
        or state.lab.profile_local_saves
        or state.play.profile_local_saves
    ):
        raise Mo2BootstrapRefusal(
            "post-activation-not-ready",
            "activated MO2 is not the exact empty Lab/Play starting state",
        )
    return existing


def _created_receipt(
    *,
    plan: BootstrapPlan,
    job: BootstrapJournal,
    collected: _CollectedSetup,
    existing: _ExistingObservation,
    package: PackageInventory,
    clock: Callable[[], datetime],
) -> BootstrapReceipt:
    projection_hash = existing.projection.adapter_state_sha256
    if projection_hash is None or existing.extra_entries is None:
        raise Mo2BootstrapRefusal(
            "post-activation-not-ready", "created MO2 has incomplete receipt evidence"
        )
    receipt_findings = {item.code: item for item in collected.plan.findings}
    _put_finding(
        receipt_findings,
        CheckState.PASSED,
        "post-activation-not-ready",
        "The activated contained MO2 instance projects as Ready.",
    )
    layout = collected.layout
    draft = BootstrapReceipt(
        schema_version=1,
        receipt_id="bootstrap-receipt-sha256:" + "0" * 64,
        qualification="VerifiedBootstrap",
        mode=BootstrapReceiptMode.CREATED,
        plan_id=plan.plan_id,
        job_id=job.job_id,
        release_id=plan.release_id,
        release_descriptor_sha256=plan.release_descriptor_sha256,
        archive_artifact_id=plan.archive.artifact_id,
        archive_metadata_sha256=plan.archive.metadata_sha256,
        archive_sha256=plan.archive.sha256,
        archive_size=plan.archive.size,
        skyrim_executable=plan.skyrim_executable,
        mo2_executable=existing.executable,
        final_root=str(layout.skyrim_mo2),
        downloads_root=str(layout.skyrim_mo2_downloads),
        mods_root=str(layout.skyrim_mo2_mods),
        profiles_root=str(layout.skyrim_mo2_profiles),
        overwrite_root=str(layout.skyrim_mo2_overwrite),
        webcache_root=str(layout.skyrim_mo2 / "webcache"),
        package_inventory_sha256=package.sha256,
        package_file_count=package.file_count,
        package_size=package.total_size,
        mutable_package_paths=collected.release.descriptor.mutable_package_paths,
        extra_entries=existing.extra_entries,
        profile_state_sha256=projection_hash,
        extractor=plan.extractor,
        verified_at=_bootstrap_timestamp(clock()),
        findings=_sorted_findings(receipt_findings.values()),
        coverage=plan.coverage,
        repairable=False,
        upgrade_supported=False,
    )
    return replace(draft, receipt_id=receipt_id_for(draft))


def _restore_create_prior(
    final_root: Path,
    prior_root: Path,
    *,
    prior_present: bool,
    prior_moved: bool,
    activated: bool,
    prior_identity: _EntryIdentity | None,
    prior_snapshot: _ManagerSnapshot | None,
    manager_ancestry: tuple[tuple[Path, _EntryIdentity], ...] | None,
    prior_parent_ancestry: tuple[tuple[Path, _EntryIdentity], ...] | None,
) -> str | None:
    if activated or not prior_moved:
        return None
    try:
        if _lstat_if_exists(final_root) is not None:
            raise Mo2ArchiveError(
                "final target appeared before preserved prior could be restored"
            )
        if (
            not prior_present
            or prior_identity is None
            or prior_snapshot is None
            or manager_ancestry is None
            or prior_parent_ancestry is None
        ):
            raise Mo2ArchiveError("preserved prior evidence is incomplete")
        _validate_captured_ancestry(manager_ancestry)
        _validate_captured_ancestry(prior_parent_ancestry)
        if _lstat_if_exists(final_root) is not None:
            raise Mo2ArchiveError(
                "final target appeared before preserved prior could be restored"
            )
        _require_tree_identity(
            prior_root,
            prior_identity,
            prior_snapshot,
            "preserved target",
        )
        _validate_captured_ancestry(manager_ancestry)
        _validate_captured_ancestry(prior_parent_ancestry)
        if _lstat_if_exists(final_root) is not None:
            raise Mo2ArchiveError(
                "final target appeared immediately before preserved prior restoration"
            )
        _replace_path(prior_root, final_root)
        _validate_captured_ancestry(manager_ancestry)
        _validate_captured_ancestry(prior_parent_ancestry)
        _require_tree_identity(
            final_root,
            prior_identity,
            prior_snapshot,
            "restored target",
        )
        return None
    except Exception as error:
        return str(error) or type(error).__name__


def _mark_create_recovery(
    store: Mo2BootstrapStore,
    observed: StoredBootstrapJournal,
    error: Exception,
    clock: Callable[[], datetime],
    rollback_error: str | None,
) -> StoredBootstrapJournal:
    try:
        current = store.load_job(observed.journal.job_id)
        if current.journal.state not in {
            BootstrapJobState.PLANNED,
            BootstrapJobState.STAGING,
            BootstrapJobState.STAGED,
            BootstrapJobState.APPLYING,
            BootstrapJobState.ACTIVATED,
        }:
            return current
        try:
            timestamp = _bootstrap_timestamp(clock())
        except Exception:
            timestamp = current.journal.updated_at
        message = str(error) or type(error).__name__
        if rollback_error is not None:
            message += f"; prior restoration failed: {rollback_error}"
        replacement = replace(
            current.journal,
            state=BootstrapJobState.RECOVERY_REQUIRED,
            updated_at=timestamp,
            error=message,
        )
        return store.transition_job(current, current.journal.state, replacement)
    except Exception:
        return observed


def _replace_path(source: Path, target: Path) -> None:
    os.replace(source, target)


def _remove_preserved_empty_prior(
    prior_root: Path,
    expected_root: _EntryIdentity,
    expected_tree: _ManagerSnapshot,
) -> None:
    _require_tree_identity(prior_root, expected_root, expected_tree, "preserved target")
    if expected_tree.inventory.file_count or any(
        entry.kind != "directory" for entry in expected_tree.entries
    ):
        raise Mo2ArchiveError("preserved prior is not an exact empty directory tree")
    ancestry = _capture_cleanup_ancestry(prior_root.parent)
    entries = {entry.relative_path: entry for entry in expected_tree.entries}
    directories = sorted(
        expected_tree.entries,
        key=lambda entry: (
            -len(PurePosixPath(entry.relative_path).parts),
            entry.relative_path.casefold(),
            entry.relative_path,
        ),
    )
    for entry in directories:
        _validate_cleanup_entry(
            prior_root,
            expected_root,
            ancestry,
            entries,
            entry,
        )
        prior_root.joinpath(*PurePosixPath(entry.relative_path).parts).rmdir()
    _validate_cleanup_root(prior_root, expected_root, ancestry)
    prior_root.rmdir()


def _apply_adopt_plan(
    *,
    plan: BootstrapPlan,
    collected: _CollectedSetup,
    store: Mo2BootstrapStore,
    process_inspector: Callable[[Path], ProcessObservation],
    command_runner: CommandRunner | None,
    free_space_reader: Callable[[Path], int],
    clock: Callable[[], datetime],
    version_reader: Callable[[Path], str | None],
) -> SetupApplyResult:
    runner = command_runner or SubprocessCommandRunner()
    try:
        job = store.create_job(plan)
    except (Mo2BootstrapStoreError, OSError) as error:
        raise Mo2BootstrapRefusal(
            "journal-invalid", f"bootstrap adoption job could not be created: {error}"
        ) from error

    journal_path = job.path.relative_to(collected.layout.root).as_posix()
    stage_inventory: PackageInventory | None = None
    try:
        _require_stopped_processes(
            _observe_processes(process_inspector, collected.layout.skyrim_mo2, {})
        )
        job = _transition_adopt_job(
            store,
            job,
            BootstrapJobState.PLANNED,
            BootstrapJobState.STAGING,
            clock,
        )
        _require_extraction_inputs_current(collected)
        stage_root = Path(job.journal.stage_root)
        stage_inventory = extract_and_inventory(
            collected.artifact.payload_path,
            collected.release.descriptor,
            Path(collected.plan.extractor.executable.path),
            stage_root,
            runner=runner,
            free_space_reader=free_space_reader,
        )
        _require_extraction_inputs_current(collected)
        job = _transition_adopt_job(
            store,
            job,
            BootstrapJobState.STAGING,
            BootstrapJobState.STAGED,
            clock,
            stage_inventory_sha256=stage_inventory.sha256,
            stage_entry_count=stage_inventory.file_count,
        )

        manager_before = _snapshot_manager_tree(collected.layout.skyrim_mo2)
        existing_app = inventory_tree(collected.layout.skyrim_mo2_app)
        comparison = compare_package_to_existing(
            stage_inventory,
            existing_app,
            collected.release.descriptor,
        )
        if not comparison.compatible:
            raise Mo2BootstrapRefusal(
                "existing-package-mismatch",
                _comparison_refusal_message(comparison),
            )
        refreshed_findings: dict[str, BootstrapFinding] = {}
        refreshed_existing = _observe_existing_manager(
            collected.layout,
            Path(plan.game_root),
            collected.release.descriptor,
            collected.listing,
            version_reader,
            refreshed_findings,
        )
        manager_after = _snapshot_manager_tree(collected.layout.skyrim_mo2)
        if manager_after != manager_before:
            raise Mo2BootstrapRefusal(
                "target-changed",
                "existing MO2 changed while ModLab was verifying it",
            )
        blocked_refresh = tuple(
            finding
            for finding in refreshed_findings.values()
            if finding.state is CheckState.BLOCKED
        )
        if (
            refreshed_existing is None
            or refreshed_existing.projection.readiness is not Mo2Readiness.READY
            or refreshed_existing.projection.adapter_state_sha256 is None
            or blocked_refresh
            or refreshed_existing.package_inventory != stage_inventory
            or refreshed_existing.extra_entries != comparison.allowed_extras
        ):
            raise Mo2BootstrapRefusal(
                "post-activation-not-ready",
                "existing MO2 no longer has complete, exact Ready package evidence",
            )

        receipt = _adoption_receipt(
            plan=plan,
            job=job.journal,
            collected=collected,
            existing=refreshed_existing,
            package=stage_inventory,
            allowed_extras=comparison.allowed_extras,
            clock=clock,
        )
        stored_receipt = store.write_receipt(receipt)
        _require_stopped_processes(
            _observe_processes(process_inspector, collected.layout.skyrim_mo2, {})
        )
        if _snapshot_manager_tree(collected.layout.skyrim_mo2) != manager_before:
            raise Mo2BootstrapRefusal(
                "target-changed",
                "existing MO2 changed before adoption verification was committed",
            )
        job = _transition_adopt_job(
            store,
            job,
            BootstrapJobState.STAGED,
            BootstrapJobState.VERIFIED,
            clock,
            receipt_id=stored_receipt.receipt.receipt_id,
        )
    except Mo2BootstrapRefusal as error:
        _mark_adopt_recovery(store, job, error, clock)
        raise
    except (BootstrapFormatError, Mo2ArchiveError, Mo2BootstrapStoreError, OSError) as error:
        _mark_adopt_recovery(store, job, error, clock)
        raise Mo2BootstrapRefusal(
            "recovery-required", f"MO2 adoption requires recovery: {error}"
        ) from error
    except Exception as error:
        _mark_adopt_recovery(store, job, error, clock)
        raise Mo2BootstrapRefusal(
            "recovery-required", f"MO2 adoption failed safely: {error}"
        ) from error

    if stage_inventory is None:
        raise Mo2BootstrapError("verified adoption has no recorded stage inventory")
    try:
        _remove_verified_stage(
            Path(job.journal.stage_root),
            collected.layout.skyrim_mo2.parent,
            job.journal.job_id,
            stage_inventory,
            collected.listing,
        )
    except (Mo2ArchiveError, OSError) as error:
        raise Mo2BootstrapRefusal(
            "recovery-required",
            f"adoption verified but its exact staging directory was not cleaned: {error}",
        ) from error

    programs = (*collected.plan.programs_launched, f"{plan.extractor.executable.path} [extract]")
    return SetupApplyResult(
        outcome=BootstrapReceiptMode.ADOPTED.value,
        plan_id=plan.plan_id,
        journal=job.journal,
        receipt=stored_receipt.receipt,
        paths_written=tuple(
            sorted(
                (
                    journal_path,
                    stored_receipt.path.relative_to(
                        collected.layout.root
                    ).as_posix(),
                ),
                key=str.casefold,
            )
        ),
        downloads=(),
        installations=(),
        manager_changes=(),
        game_changes=(),
        programs_launched=programs,
    )


def _transition_adopt_job(
    store: Mo2BootstrapStore,
    observed: StoredBootstrapJournal,
    expected: BootstrapJobState,
    target: BootstrapJobState,
    clock: Callable[[], datetime],
    **changes: object,
) -> StoredBootstrapJournal:
    replacement = replace(
        observed.journal,
        state=target,
        updated_at=_bootstrap_timestamp(clock()),
        error=None,
        **changes,
    )
    return store.transition_job(observed, expected, replacement)


def _mark_adopt_recovery(
    store: Mo2BootstrapStore,
    observed: StoredBootstrapJournal,
    error: Exception,
    clock: Callable[[], datetime],
) -> StoredBootstrapJournal:
    try:
        current = store.load_job(observed.journal.job_id)
        if current.journal.state not in {
            BootstrapJobState.PLANNED,
            BootstrapJobState.STAGING,
            BootstrapJobState.STAGED,
        }:
            return current
        try:
            timestamp = _bootstrap_timestamp(clock())
        except Exception:
            timestamp = current.journal.updated_at
        replacement = replace(
            current.journal,
            state=BootstrapJobState.RECOVERY_REQUIRED,
            updated_at=timestamp,
            error=str(error) or type(error).__name__,
        )
        return store.transition_job(current, current.journal.state, replacement)
    except Exception:
        return observed


def _adoption_receipt(
    *,
    plan: BootstrapPlan,
    job: BootstrapJournal,
    collected: _CollectedSetup,
    existing: _ExistingObservation,
    package: PackageInventory,
    allowed_extras: tuple[str, ...],
    clock: Callable[[], datetime],
) -> BootstrapReceipt:
    projection_hash = existing.projection.adapter_state_sha256
    if projection_hash is None:
        raise Mo2BootstrapRefusal(
            "post-activation-not-ready", "Ready MO2 projection has no state identity"
        )
    layout = collected.layout
    draft = BootstrapReceipt(
        schema_version=1,
        receipt_id="bootstrap-receipt-sha256:" + "0" * 64,
        qualification="VerifiedBootstrap",
        mode=BootstrapReceiptMode.ADOPTED,
        plan_id=plan.plan_id,
        job_id=job.job_id,
        release_id=plan.release_id,
        release_descriptor_sha256=plan.release_descriptor_sha256,
        archive_artifact_id=plan.archive.artifact_id,
        archive_metadata_sha256=plan.archive.metadata_sha256,
        archive_sha256=plan.archive.sha256,
        archive_size=plan.archive.size,
        skyrim_executable=plan.skyrim_executable,
        mo2_executable=existing.executable,
        final_root=str(layout.skyrim_mo2),
        downloads_root=str(layout.skyrim_mo2_downloads),
        mods_root=str(layout.skyrim_mo2_mods),
        profiles_root=str(layout.skyrim_mo2_profiles),
        overwrite_root=str(layout.skyrim_mo2_overwrite),
        webcache_root=str(layout.skyrim_mo2 / "webcache"),
        package_inventory_sha256=package.sha256,
        package_file_count=package.file_count,
        package_size=package.total_size,
        mutable_package_paths=collected.release.descriptor.mutable_package_paths,
        extra_entries=allowed_extras,
        profile_state_sha256=projection_hash,
        extractor=plan.extractor,
        verified_at=_bootstrap_timestamp(clock()),
        findings=collected.plan.findings,
        coverage=plan.coverage,
        repairable=False,
        upgrade_supported=False,
    )
    return replace(draft, receipt_id=receipt_id_for(draft))


def _bootstrap_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise Mo2BootstrapError("bootstrap clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _comparison_refusal_message(comparison) -> str:
    details = []
    if comparison.missing:
        details.append("missing: " + ", ".join(comparison.missing))
    if comparison.modified:
        details.append("modified: " + ", ".join(comparison.modified))
    if comparison.unapproved_extras:
        details.append("unapproved extras: " + ", ".join(comparison.unapproved_extras))
    return "existing MO2 does not exactly match the retained package (" + "; ".join(details) + ")"


def _snapshot_manager_tree(root: Path) -> _ManagerSnapshot:
    source = Path(root)
    inventory = inventory_tree(source)
    entries: list[_EntryIdentity] = []

    def walk(directory: Path, relative: PurePosixPath) -> None:
        try:
            with os.scandir(directory) as scanner:
                children = sorted(
                    scanner,
                    key=lambda item: (item.name.casefold(), item.name),
                )
        except OSError as error:
            raise Mo2ArchiveError(
                f"cannot enumerate manager directory {directory}: {error}"
            ) from error
        for child in children:
            path = Path(child.path)
            child_relative = relative / child.name
            try:
                metadata = path.lstat()
            except OSError as error:
                raise Mo2ArchiveError(
                    f"cannot inspect manager entry {child_relative.as_posix()}: {error}"
                ) from error
            if child.is_symlink() or _redirected(path, metadata):
                raise Mo2ArchiveError(
                    f"manager entry {child_relative.as_posix()} is redirected"
                )
            if stat.S_ISDIR(metadata.st_mode):
                kind = "directory"
            elif stat.S_ISREG(metadata.st_mode):
                kind = "file"
            else:
                raise Mo2ArchiveError(
                    f"manager entry {child_relative.as_posix()} is not regular"
                )
            entries.append(
                _entry_identity(child_relative.as_posix(), kind, metadata)
            )
            if kind == "directory":
                walk(path, child_relative)

    walk(source, PurePosixPath())
    return _ManagerSnapshot(inventory=inventory, entries=tuple(entries))


def _entry_identity(relative_path: str, kind: str, metadata) -> _EntryIdentity:
    is_file = kind == "file"
    return _EntryIdentity(
        relative_path=relative_path,
        kind=kind,
        device=int(getattr(metadata, "st_dev", 0)),
        inode=int(getattr(metadata, "st_ino", 0)),
        mode_type=stat.S_IFMT(metadata.st_mode),
        file_attributes=int(getattr(metadata, "st_file_attributes", 0)),
        size=int(metadata.st_size) if is_file else None,
        modified_ns=int(metadata.st_mtime_ns) if is_file else None,
        changed_ns=int(metadata.st_ctime_ns) if is_file else None,
    )


def _remove_verified_stage(
    stage_root: Path,
    staging_parent: Path,
    job_id: str,
    expected: PackageInventory,
    listing: ArchiveListing,
) -> None:
    job_hex = job_id.removeprefix("bootstrap-job:")
    expected_name = f".skyrim-se-ae.modlab-stage-{job_hex}"
    if stage_root.name != expected_name or stage_root.parent != staging_parent:
        raise Mo2ArchiveError("staging path does not match its exact job identity")
    ancestry = _capture_cleanup_ancestry(staging_parent)
    stage_metadata = _direct_cleanup_metadata(stage_root, "staging root")
    if not stat.S_ISDIR(stage_metadata.st_mode):
        raise Mo2ArchiveError("staging root is not a direct directory")
    stage_identity = _entry_identity(".", "directory", stage_metadata)
    snapshot = _snapshot_manager_tree(stage_root)
    if snapshot.inventory != expected:
        raise Mo2ArchiveError("staging inventory changed before cleanup")

    expected_directories: set[str] = set()
    for file in expected.files:
        path = PurePosixPath(file.relative_path)
        for depth in range(1, len(path.parts)):
            expected_directories.add(PurePosixPath(*path.parts[:depth]).as_posix())
    for entry in listing.entries:
        if entry.kind == "directory":
            path = PurePosixPath(entry.relative_path)
            for depth in range(1, len(path.parts) + 1):
                expected_directories.add(PurePosixPath(*path.parts[:depth]).as_posix())
    observed_directories = {
        entry.relative_path for entry in snapshot.entries if entry.kind == "directory"
    }
    if observed_directories != expected_directories:
        raise Mo2ArchiveError("staging directory entries changed before cleanup")

    by_path = {entry.relative_path: entry for entry in snapshot.entries}
    expected_files = {file.relative_path: file for file in expected.files}
    files = [entry for entry in snapshot.entries if entry.kind == "file"]
    directories = [entry for entry in snapshot.entries if entry.kind == "directory"]
    for entry in files:
        target = stage_root.joinpath(*PurePosixPath(entry.relative_path).parts)
        _validate_cleanup_entry(
            stage_root,
            stage_identity,
            ancestry,
            by_path,
            entry,
        )
        expected_file = expected_files[entry.relative_path]
        observed_hash, observed_size = _stable_direct_file_hash(
            target,
            f"verified staging file {entry.relative_path}",
        )
        if (
            observed_hash != expected_file.sha256
            or observed_size != expected_file.size
        ):
            raise Mo2ArchiveError(
                f"staging file bytes changed before cleanup: {entry.relative_path}"
            )
        _validate_cleanup_entry(
            stage_root,
            stage_identity,
            ancestry,
            by_path,
            entry,
        )
        target.unlink()
    directories.sort(
        key=lambda entry: (
            -len(PurePosixPath(entry.relative_path).parts),
            entry.relative_path.casefold(),
            entry.relative_path,
        )
    )
    for entry in directories:
        _validate_cleanup_entry(
            stage_root,
            stage_identity,
            ancestry,
            by_path,
            entry,
        )
        stage_root.joinpath(*PurePosixPath(entry.relative_path).parts).rmdir()
    _validate_cleanup_root(stage_root, stage_identity, ancestry)
    stage_root.rmdir()


def _capture_cleanup_ancestry(
    staging_parent: Path,
) -> tuple[tuple[Path, _EntryIdentity], ...]:
    result: list[tuple[Path, _EntryIdentity]] = []
    for path in (staging_parent, *staging_parent.parents):
        metadata = _direct_cleanup_metadata(path, "staging ancestor")
        if not stat.S_ISDIR(metadata.st_mode):
            raise Mo2ArchiveError(f"staging ancestor is not a directory: {path}")
        result.append((path, _entry_identity(str(path), "directory", metadata)))
    return tuple(result)


def _validate_captured_ancestry(
    ancestry: tuple[tuple[Path, _EntryIdentity], ...],
) -> None:
    for path, expected in ancestry:
        metadata = _direct_cleanup_metadata(path, "staging ancestor")
        current = _entry_identity(str(path), "directory", metadata)
        if current != expected:
            raise Mo2ArchiveError(f"staging ancestor identity changed: {path}")


def _validate_cleanup_root(
    stage_root: Path,
    expected_stage: _EntryIdentity,
    ancestry: tuple[tuple[Path, _EntryIdentity], ...],
) -> None:
    _validate_captured_ancestry(ancestry)
    metadata = _direct_cleanup_metadata(stage_root, "staging root")
    if not stat.S_ISDIR(metadata.st_mode):
        raise Mo2ArchiveError("staging root is no longer a directory")
    if _entry_identity(".", "directory", metadata) != expected_stage:
        raise Mo2ArchiveError("staging root identity changed before cleanup")


def _validate_cleanup_entry(
    stage_root: Path,
    expected_stage: _EntryIdentity,
    ancestry: tuple[tuple[Path, _EntryIdentity], ...],
    entries: dict[str, _EntryIdentity],
    expected: _EntryIdentity,
) -> None:
    _validate_cleanup_root(stage_root, expected_stage, ancestry)
    relative = PurePosixPath(expected.relative_path)
    for depth in range(1, len(relative.parts)):
        parent_relative = PurePosixPath(*relative.parts[:depth]).as_posix()
        parent_expected = entries.get(parent_relative)
        if parent_expected is None or parent_expected.kind != "directory":
            raise Mo2ArchiveError(
                f"staging ancestry was not recorded: {parent_relative}"
            )
        parent = stage_root.joinpath(*relative.parts[:depth])
        metadata = _direct_cleanup_metadata(parent, "staging entry ancestor")
        parent_observed = _entry_identity(parent_relative, "directory", metadata)
        if parent_observed != parent_expected:
            raise Mo2ArchiveError(
                "staging entry ancestor changed before cleanup: "
                f"{parent_relative} ({parent_expected!r} != {parent_observed!r})"
            )
    target = stage_root.joinpath(*relative.parts)
    metadata = _direct_cleanup_metadata(target, "staging entry")
    observed_kind = (
        "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
        if stat.S_ISREG(metadata.st_mode)
        else "other"
    )
    if observed_kind == "other" or _entry_identity(
        expected.relative_path,
        observed_kind,
        metadata,
    ) != expected:
        raise Mo2ArchiveError(
            f"staging entry identity changed before cleanup: {expected.relative_path}"
        )


def _direct_cleanup_metadata(path: Path, label: str):
    try:
        metadata = path.lstat()
    except OSError as error:
        raise Mo2ArchiveError(f"cannot inspect {label} {path}: {error}") from error
    if _redirected(path, metadata):
        raise Mo2ArchiveError(f"{label} is redirected: {path}")
    return metadata


def _select_environment(
    layout: WorkspaceLayout,
    supplied_steam_root: Path | None,
    version_reader: Callable[[Path], str | None],
) -> _EnvironmentSelection:
    del version_reader  # Reserved for status observation after discovery.
    store = SkyrimEnvironmentStore(layout.root)
    try:
        snapshot = store.load()
    except SkyrimEnvironmentNotConfiguredError:
        snapshot = None
    except SkyrimEnvironmentStoreError as error:
        raise Mo2BootstrapRefusal(
            "environment-mismatch",
            f"retained Skyrim environment is invalid: {error}",
        ) from error

    if snapshot is None and supplied_steam_root is None:
        raise Mo2BootstrapRefusal(
            "environment-mismatch",
            "An explicit Steam root is required before Skyrim is configured.",
        )

    configured = (
        None if snapshot is None else Path(snapshot.configuration.steam_root)
    )
    selected = configured if supplied_steam_root is None else Path(supplied_steam_root)
    assert selected is not None
    selected = selected.expanduser().resolve(strict=False)
    if configured is not None and not _same_path(selected, configured):
        raise Mo2BootstrapRefusal(
            "environment-mismatch",
            "The supplied Steam root conflicts with retained Skyrim intent.",
        )
    baseline_id = (
        None if snapshot is None else snapshot.configuration.baseline_checkpoint_id
    )
    return _EnvironmentSelection(
        steam_root=selected,
        snapshot=snapshot,
        baseline_id=baseline_id,
        baseline_status="NoBaseline" if baseline_id is None else "Matched",
    )


def _load_release(path: Path) -> LoadedMo2Release:
    try:
        release = load_mo2_release(Path(path))
    except (Mo2ReleaseFormatError, OSError) as error:
        code = "release-modified" if "changed" in str(error).casefold() else "release-unsupported"
        raise Mo2BootstrapRefusal(
            code, f"curated MO2 release descriptor is not trusted: {error}"
        ) from error
    if release.descriptor.release_id != "mo2.windows-portable.2.5.2":
        raise Mo2BootstrapRefusal(
            "release-unsupported",
            f"unsupported MO2 release: {release.descriptor.release_id}",
        )
    try:
        _require_direct_regular_file(release.path, "release descriptor")
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "release-modified", f"release descriptor path is unsafe: {error}"
        ) from error
    return release


def _observe_artifact(
    layout: WorkspaceLayout, artifact_id: str
) -> _ArtifactObservation:
    match = re.fullmatch(r"archive-sha256:([0-9a-f]{64})", artifact_id or "")
    if match is None:
        raise Mo2BootstrapRefusal(
            "artifact-missing", "artifact ID must use archive-sha256:<hash>"
        )
    digest = match.group(1)
    metadata_path = layout.metadata / "artifacts" / f"{digest}.json"
    try:
        _require_direct_file_under(
            layout.root,
            metadata_path,
            "artifact metadata",
        )
        metadata_data = _stable_direct_file_bytes(metadata_path, "artifact metadata")
        raw = json.loads(
            metadata_data.decode("utf-8"),
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        record = artifact_from_dict(raw)
    except FileNotFoundError as error:
        raise Mo2BootstrapRefusal(
            "artifact-missing", f"retained archive metadata is missing: {metadata_path}"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError, ArtifactFormatError, ValueError) as error:
        raise Mo2BootstrapRefusal(
            "artifact-modified", f"retained archive metadata is invalid: {error}"
        ) from error
    if record.artifact_id != artifact_id:
        raise Mo2BootstrapRefusal(
            "artifact-modified", "retained archive metadata has the wrong identity"
        )
    payload = layout.root.joinpath(*PurePosixPath(record.stored_relative_path).parts)
    try:
        _require_direct_file_under(
            layout.root,
            payload,
            "retained archive",
        )
        payload_hash, payload_size = _stable_direct_file_hash(payload, "retained archive")
    except FileNotFoundError as error:
        raise Mo2BootstrapRefusal(
            "artifact-missing", f"retained archive payload is missing: {payload}"
        ) from error
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "artifact-modified", f"retained archive payload is unsafe: {error}"
        ) from error
    if payload_hash != record.sha256 or payload_size != record.size:
        raise Mo2BootstrapRefusal(
            "artifact-modified", "retained archive bytes differ from their metadata"
        )
    return _ArtifactObservation(
        record=record,
        metadata_sha256=hashlib.sha256(metadata_data).hexdigest(),
        metadata_data=metadata_data,
        payload_path=payload,
    )


def _require_release_artifact(
    release: Mo2ReleaseDescriptor, artifact: ArchiveArtifact
) -> None:
    if (
        artifact.original_name != release.archive_name
        or artifact.sha256 != release.archive_sha256
        or artifact.size != release.archive_size
        or PurePosixPath(artifact.stored_relative_path).suffix.casefold() != ".7z"
    ):
        raise Mo2BootstrapRefusal(
            "artifact-release-mismatch",
            "retained archive identity does not match the curated MO2 release",
        )


def _observe_extractor(path: Path, runner: CommandRunner) -> ExtractorIdentity:
    try:
        return observe_bsdtar(Path(path), runner=runner)
    except (Mo2ArchiveError, OSError) as error:
        message = str(error)
        folded = message.casefold()
        if "missing" in folded or "does not exist" in folded:
            code = "extractor-missing"
        elif "resolve to" in folded or "redirect" in folded:
            code = "extractor-redirected"
        elif "changed" in folded:
            code = "extractor-changed"
        else:
            code = "extractor-failed"
        raise Mo2BootstrapRefusal(code, f"system bsdtar is not trusted: {message}") from error


def _observe_archive_listing(
    archive_path: Path,
    release: Mo2ReleaseDescriptor,
    extractor_path: Path,
    runner: CommandRunner,
):
    try:
        return preflight_archive(
            archive_path,
            release,
            extractor_path,
            runner=runner,
        )
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "extractor-failed", f"system bsdtar could not be launched: {error}"
        ) from error
    except Mo2ArchiveError as error:
        message = str(error)
        mismatch_words = (
            "does not match",
            "different counts",
            "count differs",
            "mismatch",
        )
        code = (
            "archive-listing-mismatch"
            if any(word in message.casefold() for word in mismatch_words)
            else "archive-listing-unsafe"
        )
        raise Mo2BootstrapRefusal(code, f"MO2 archive preflight failed: {message}") from error


def _discover_ready_skyrim(
    steam_root: Path,
    version_reader: Callable[[Path], str | None],
) -> SkyrimDiscoveryReport:
    try:
        report = discover_skyrim_steam(steam_root, version_reader=version_reader)
    except (OSError, ValueError) as error:
        raise Mo2BootstrapRefusal(
            "skyrim-discovery-blocked", f"Skyrim discovery failed: {error}"
        ) from error
    blocking = tuple(
        item for item in report.findings if item.state is CheckState.BLOCKED
    )
    if (
        report.app_id != "489830"
        or report.install_state_flags != 4
        or report.game_root is None
        or report.executable is None
        or report.executable.file_version is None
        or report.executable.compatibility_runtime is None
        or blocking
    ):
        detail = "; ".join(f"{item.code}: {item.message}" for item in blocking)
        raise Mo2BootstrapRefusal(
            "skyrim-discovery-blocked",
            "Steam app 489830 is not Ready" + (f": {detail}" if detail else "."),
        )
    return report


def _game_executable_identity(
    discovery: SkyrimDiscoveryReport, game_root: Path
) -> FileIdentity:
    executable = discovery.executable
    if executable is None:
        raise Mo2BootstrapRefusal(
            "skyrim-discovery-blocked", "SkyrimSE.exe identity is missing"
        )
    return FileIdentity(
        path=str(game_root / executable.relative_path),
        sha256=executable.sha256,
        size=executable.size,
    )


def _require_environment_game_match(
    snapshot: ConfigurationSnapshot | None,
    discovery: SkyrimDiscoveryReport,
) -> None:
    if snapshot is None:
        return
    configured = snapshot.configuration
    if (
        discovery.game_root is None
        or not _same_path(configured.steam_root, discovery.steam_root)
        or not _same_path(configured.game_root, discovery.game_root)
    ):
        raise Mo2BootstrapRefusal(
            "environment-mismatch",
            "discovered Skyrim paths conflict with retained environment intent",
        )


def _select_documents_root(value: Path | None) -> Path:
    if value is not None:
        return Path(value).expanduser().absolute()
    try:
        return windows_documents_root()
    except ProcessInspectionError as error:
        raise Mo2BootstrapRefusal(
            "profile-seed-changed", f"Windows Documents could not be resolved: {error}"
        ) from error


def _observe_seed(game_root: Path, documents_root: Path) -> ProfileSeedEvidence:
    try:
        return observe_profile_seed(game_root, documents_root)
    except Mo2BootstrapConfigError as error:
        raise Mo2BootstrapRefusal(
            "profile-seed-changed", f"profile seed is incomplete or unsafe: {error}"
        ) from error


def _classify_target_finding(
    layout: WorkspaceLayout,
    target: TargetSnapshot,
    findings: dict[str, BootstrapFinding],
) -> None:
    if target.kind != "Blocked":
        _put_finding(
            findings,
            CheckState.PASSED,
            "target-unknown-nonempty",
            f"The contained MO2 target was classified as {target.kind}.",
        )
        return
    metadata = _lstat_if_exists(layout.skyrim_mo2)
    redirected = metadata is not None and _redirected(layout.skyrim_mo2, metadata)
    _put_finding(
        findings,
        CheckState.BLOCKED,
        "target-redirected" if redirected else "target-unknown-nonempty",
        (
            "The contained MO2 target is redirected."
            if redirected
            else "The contained MO2 target has an unknown nonempty layout."
        ),
    )


def _observe_processes(
    inspector: Callable[[Path], ProcessObservation],
    target_root: Path,
    findings: dict[str, BootstrapFinding],
) -> ProcessObservation:
    try:
        observation = inspector(target_root)
    except Exception as error:
        observation = ProcessObservation(complete=False, relevant=(), error=str(error))
    if not isinstance(observation, ProcessObservation) or not observation.complete:
        if not isinstance(observation, ProcessObservation):
            observation = ProcessObservation(
                complete=False,
                relevant=(),
                error="process inspector returned an invalid result",
            )
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "process-inspection-unknown",
            "MO2 process inspection is incomplete: " + (observation.error or "unknown error"),
        )
        return observation
    _put_finding(
        findings,
        CheckState.WARNING if observation.relevant else CheckState.PASSED,
        "mo2-process-running",
        (
            "Relevant MO2 processes must be closed before apply."
            if observation.relevant
            else "No relevant MO2 process is running."
        ),
    )
    return observation


def _read_free_space(
    reader: Callable[[Path], int],
    staging_parent: Path,
) -> int:
    try:
        available = reader(staging_parent)
    except Exception as error:
        raise Mo2BootstrapRefusal(
            "disk-space-insufficient", f"free disk space could not be measured: {error}"
        ) from error
    if type(available) is not int or available < 0:
        raise Mo2BootstrapRefusal(
            "disk-space-insufficient", "free disk space reader returned an invalid value"
        )
    return available


def _record_free_space(
    available: int,
    required: int,
    *,
    staging_required: bool,
    findings: dict[str, BootstrapFinding],
) -> None:
    if not staging_required:
        _put_finding(
            findings,
            CheckState.PASSED,
            "disk-space-insufficient",
            "No staging space is required for this verified no-op.",
        )
        return
    _put_finding(
        findings,
        CheckState.PASSED if available >= required else CheckState.BLOCKED,
        "disk-space-insufficient",
        (
            f"Available free space satisfies the {required}-byte staging requirement."
            if available >= required
            else f"Available free space is below the {required}-byte staging requirement."
        ),
    )


def _observe_recovery_state(
    layout: WorkspaceLayout, findings: dict[str, BootstrapFinding]
) -> None:
    staging_parent = layout.skyrim_mo2.parent
    try:
        stage_entries = tuple(staging_parent.iterdir())
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "stage-collision", f"staging parent cannot be inspected: {error}"
        ) from error
    store = Mo2BootstrapStore(layout.root)
    active_jobs: set[str] = set()
    jobs_root = layout.mo2_bootstrap_jobs
    try:
        entries = tuple(jobs_root.iterdir()) if jobs_root.exists() else ()
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "journal-invalid", f"bootstrap jobs cannot be inspected: {error}"
        ) from error
    for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
        if entry.name == "plans":
            continue
        if not _JOB_DIRECTORY.fullmatch(entry.name):
            _put_finding(
                findings,
                CheckState.BLOCKED,
                "journal-invalid",
                f"Unexpected bootstrap job-store entry: {entry.name}.",
            )
            continue
        job_id = f"bootstrap-job:{entry.name}"
        try:
            journal = store.load_job(job_id).journal
        except Mo2BootstrapStoreError as error:
            _put_finding(
                findings,
                CheckState.BLOCKED,
                "journal-invalid",
                f"Bootstrap journal {entry.name} is invalid: {error}",
            )
            continue
        if journal.state.value not in {"Verified", "Recovered"}:
            active_jobs.add(entry.name)
            _put_finding(
                findings,
                CheckState.BLOCKED,
                "recovery-required",
                f"Bootstrap job {entry.name} requires recovery before another apply.",
            )
    for entry in sorted(stage_entries, key=lambda item: (item.name.casefold(), item.name)):
        stage = _STAGE_DIRECTORY.fullmatch(entry.name)
        if stage is None:
            continue
        code = "stage-collision" if stage.group(1) in active_jobs else "orphan-stage"
        _put_finding(
            findings,
            CheckState.BLOCKED,
            code,
            f"Bootstrap staging directory must be recovered first: {entry.name}.",
        )


def _observe_existing_manager(
    layout: WorkspaceLayout,
    game_root: Path,
    release: Mo2ReleaseDescriptor,
    listing: ArchiveListing,
    version_reader: Callable[[Path], str | None],
    findings: dict[str, BootstrapFinding],
) -> _ExistingObservation | None:
    try:
        report = inspect_skyrim_mo2(
            layout.skyrim_mo2_app,
            game_root,
            workspace_root=layout.root,
            version_reader=version_reader,
        )
        projection = project_mo2_state(report)
    except (OSError, ValueError) as error:
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "post-activation-not-ready",
            f"Existing MO2 could not be inspected safely: {error}",
        )
        return None
    executable = projection.observation_context.executable
    if projection.readiness is not Mo2Readiness.READY or executable is None:
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "post-activation-not-ready",
            "Existing MO2 does not project as the exact Ready Lab/Play layout.",
        )
        return None
    identity = FileIdentity(
        path=str(layout.skyrim_mo2_app / executable.relative_path),
        sha256=executable.sha256,
        size=executable.size,
    )
    expected = release.executable
    if (
        executable.relative_path != expected.relative_path
        or executable.sha256 != expected.sha256
        or executable.size != expected.size
        or executable.file_version != expected.file_version
    ):
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "existing-package-mismatch",
            "Existing ModOrganizer.exe does not match the curated package identity.",
        )
        return _ExistingObservation(
            projection=projection,
            executable=identity,
            package_inventory=None,
            extra_entries=None,
        )
    _put_finding(
        findings,
        CheckState.PASSED,
        "existing-package-mismatch",
        "Existing MO2 is Ready and its executable matches the curated package.",
    )
    _put_finding(
        findings,
        CheckState.PASSED,
        "post-activation-not-ready",
        "Existing MO2 projects as the exact Ready Lab/Play layout.",
    )
    try:
        package_inventory, extra_entries = _inventory_existing_package_state(
            layout.skyrim_mo2_app,
            listing,
            release,
        )
    except Mo2ArchiveError as error:
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "existing-package-mismatch",
            f"Existing MO2 package paths are unsafe: {error}",
        )
        package_inventory, extra_entries = None, None
    return _ExistingObservation(
        projection=projection,
        executable=identity,
        package_inventory=package_inventory,
        extra_entries=extra_entries,
    )


def _inventory_existing_package_state(
    app_root: Path,
    listing: ArchiveListing,
    release: Mo2ReleaseDescriptor,
) -> tuple[PackageInventory | None, tuple[str, ...] | None]:
    full_inventory = inventory_tree(app_root)
    by_path = {
        item.relative_path.casefold(): item for item in full_inventory.files
    }
    files: list[PackageFile] = []
    for entry in listing.entries:
        if entry.kind != "file":
            continue
        relative = PurePosixPath(entry.relative_path)
        observed = by_path.get(relative.as_posix().casefold())
        if observed is None:
            return None, None
        files.append(
            PackageFile(
                relative_path=relative.as_posix(),
                sha256=observed.sha256,
                size=observed.size,
            )
        )
    ordered = tuple(
        sorted(files, key=lambda item: (item.relative_path.casefold(), item.relative_path))
    )
    if len(ordered) != release.package_file_count:
        return None, None
    canonical = json.dumps(
        [[item.relative_path, item.sha256, item.size] for item in ordered],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    package = PackageInventory(
        files=ordered,
        total_size=sum(item.size for item in ordered),
        sha256=hashlib.sha256(canonical).hexdigest(),
    )
    try:
        comparison = compare_package_to_existing(package, full_inventory, release)
    except Mo2ArchiveError:
        return package, None
    return (
        package,
        comparison.allowed_extras if comparison.compatible else None,
    )


def _observe_baseline(
    environment: _EnvironmentSelection,
    layout: WorkspaceLayout,
    version_reader: Callable[[Path], str | None],
    findings: dict[str, BootstrapFinding],
) -> str:
    if environment.snapshot is None or environment.baseline_id is None:
        _put_finding(
            findings,
            CheckState.PASSED,
            "baseline-drifted",
            "No observed baseline is selected.",
        )
        return "NoBaseline"
    report = get_skyrim_status(
        layout.root,
        skyrim_version_reader=version_reader,
        mo2_version_reader=version_reader,
    )
    if report.outcome not in {SkyrimStatusOutcome.MATCHED, SkyrimStatusOutcome.NO_BASELINE}:
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "baseline-drifted",
            f"Selected Skyrim baseline status is {report.outcome.value}.",
        )
        return report.outcome.value
    _put_finding(
        findings,
        CheckState.PASSED,
        "baseline-drifted",
        f"Selected Skyrim baseline status is {report.outcome.value}.",
    )
    return report.outcome.value


def _find_compatible_receipt(
    layout: WorkspaceLayout,
    release: LoadedMo2Release,
    archive: ArchiveEvidence,
    skyrim_executable: FileIdentity,
    extractor: ExtractorIdentity,
    existing: _ExistingObservation | None,
    findings: dict[str, BootstrapFinding],
) -> StoredBootstrapReceipt | None:
    match = BootstrapReceiptMatch(
        release_id=release.descriptor.release_id,
        release_descriptor_sha256=release.sha256,
        archive_artifact_id=archive.artifact_id,
        archive_metadata_sha256=archive.metadata_sha256,
        archive_sha256=archive.sha256,
        archive_size=archive.size,
        skyrim_executable=skyrim_executable,
        final_root=str(layout.skyrim_mo2),
    )
    try:
        candidate = Mo2BootstrapStore(layout.root).find_compatible_receipt(match)
    except Mo2BootstrapStoreError as error:
        _put_finding(
            findings,
            CheckState.BLOCKED,
            "receipt-invalid",
            f"Bootstrap receipt store is invalid: {error}",
        )
        return None
    if candidate is None or existing is None:
        return None
    receipt = candidate.receipt
    projection_hash = existing.projection.adapter_state_sha256
    package = existing.package_inventory
    extra_entries = existing.extra_entries
    roots_match = all(
        (
            _same_path(receipt.downloads_root, layout.skyrim_mo2_downloads),
            _same_path(receipt.mods_root, layout.skyrim_mo2_mods),
            _same_path(receipt.profiles_root, layout.skyrim_mo2_profiles),
            _same_path(receipt.overwrite_root, layout.skyrim_mo2_overwrite),
            _same_path(receipt.webcache_root, layout.skyrim_mo2 / "webcache"),
        )
    )
    if (
        receipt.extractor != extractor
        or receipt.mo2_executable != existing.executable
        or package is None
        or receipt.package_inventory_sha256 != package.sha256
        or receipt.package_file_count != package.file_count
        or receipt.package_size != package.total_size
        or extra_entries is None
        or receipt.extra_entries != extra_entries
        or projection_hash is None
        or receipt.profile_state_sha256 != projection_hash
        or not roots_match
    ):
        return None
    return candidate


def _require_stable_inputs(
    *,
    layout: WorkspaceLayout,
    environment: _EnvironmentSelection,
    release: LoadedMo2Release,
    artifact: _ArtifactObservation,
    discovery: SkyrimDiscoveryReport,
    target: TargetSnapshot,
    seed: ProfileSeedEvidence,
    extractor: ExtractorIdentity,
    listing: ArchiveListing,
    existing: _ExistingObservation | None,
    compatible_receipt: StoredBootstrapReceipt | None,
    receipt_invalid_finding: BootstrapFinding | None,
    archive_evidence: ArchiveEvidence,
    game_executable: FileIdentity,
    documents_root: Path,
    version_reader: Callable[[Path], str | None],
) -> None:
    if classify_mo2_target(layout) != target:
        raise Mo2BootstrapRefusal(
            "target-changed", "MO2 target changed during its stability re-read"
        )
    repeated_discovery = _discover_ready_skyrim(environment.steam_root, version_reader)
    if repeated_discovery != discovery:
        raise Mo2BootstrapRefusal(
            "skyrim-discovery-blocked",
            "Skyrim evidence changed during its stability re-read",
        )
    repeated_release = _load_release(release.path)
    if repeated_release != release:
        raise Mo2BootstrapRefusal(
            "release-modified", "release descriptor changed during stability re-read"
        )
    repeated_artifact = _observe_artifact(layout, artifact.record.artifact_id)
    if repeated_artifact != artifact:
        raise Mo2BootstrapRefusal(
            "artifact-modified", "retained archive changed during stability re-read"
        )
    if environment.snapshot is not None:
        try:
            repeated_environment = SkyrimEnvironmentStore(layout.root).load()
        except (SkyrimEnvironmentNotConfiguredError, SkyrimEnvironmentStoreError) as error:
            raise Mo2BootstrapRefusal(
                "environment-mismatch",
                f"retained environment changed during stability re-read: {error}",
            ) from error
        if repeated_environment != environment.snapshot:
            raise Mo2BootstrapRefusal(
                "environment-mismatch",
                "retained environment changed during its stability re-read",
            )
    repeated_seed = _observe_seed(Path(discovery.game_root or ""), documents_root)
    if repeated_seed != seed:
        raise Mo2BootstrapRefusal(
            "profile-seed-changed",
            "profile seed changed during its stability re-read",
        )
    try:
        extractor_hash, extractor_size = _stable_direct_file_hash(
            Path(extractor.executable.path),
            "system bsdtar",
        )
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "extractor-changed",
            f"system bsdtar changed during stability re-read: {error}",
        ) from error
    if (
        extractor_hash != extractor.executable.sha256
        or extractor_size != extractor.executable.size
    ):
        raise Mo2BootstrapRefusal(
            "extractor-changed",
            "system bsdtar changed during its stability re-read",
        )
    repeated_existing: _ExistingObservation | None = None
    if target.kind == "Existing":
        repeated_existing = _observe_existing_manager(
            layout,
            Path(discovery.game_root or ""),
            release.descriptor,
            listing,
            version_reader,
            {},
        )
        if repeated_existing != existing:
            raise Mo2BootstrapRefusal(
                "target-changed",
                "existing MO2 evidence changed during its stability re-read",
            )
    repeated_baseline = _observe_baseline(
        environment,
        layout,
        version_reader,
        {},
    )
    if repeated_baseline != environment.baseline_status:
        raise Mo2BootstrapRefusal(
            "baseline-drifted",
            "baseline status changed during its stability re-read",
        )
    repeated_receipt_findings: dict[str, BootstrapFinding] = {}
    repeated_receipt = _find_compatible_receipt(
        layout,
        release,
        archive_evidence,
        game_executable,
        extractor,
        repeated_existing,
        repeated_receipt_findings,
    )
    if (
        repeated_receipt != compatible_receipt
        or repeated_receipt_findings.get("receipt-invalid")
        != receipt_invalid_finding
    ):
        raise Mo2BootstrapRefusal(
            "receipt-invalid",
            "compatible receipt evidence changed during its stability re-read",
        )


def _disposition_for(
    target: TargetSnapshot,
    findings,
    receipt: StoredBootstrapReceipt | None,
) -> BootstrapDisposition:
    if any(item.state is CheckState.BLOCKED for item in findings):
        return BootstrapDisposition.BLOCKED
    if target.kind == "Empty":
        return BootstrapDisposition.CREATE
    if target.kind == "Existing":
        return (
            BootstrapDisposition.ALREADY_MANAGED
            if receipt is not None
            else BootstrapDisposition.ADOPT
        )
    return BootstrapDisposition.BLOCKED


def _release_descriptor_reference(path: Path) -> str:
    source = Path(path).resolve(strict=False)
    repository = Path(__file__).resolve().parents[3]
    try:
        return source.relative_to(repository).as_posix()
    except ValueError as error:
        raise Mo2BootstrapRefusal(
            "release-unsupported",
            "release descriptor must be retained inside the ModLab repository",
        ) from error


def _put_finding(
    findings: dict[str, BootstrapFinding],
    state: CheckState,
    code: str,
    message: str,
) -> None:
    if code not in BOOTSTRAP_FINDING_CODES:
        raise Mo2BootstrapError(f"unknown bootstrap finding code: {code}")
    replacement = BootstrapFinding(state=state, code=code, message=message)
    current = findings.get(code)
    if current is None or _finding_rank(state) > _finding_rank(current.state):
        findings[code] = replacement


def _finding_rank(state: CheckState) -> int:
    return {
        CheckState.PASSED: 0,
        CheckState.WARNING: 1,
        CheckState.UNKNOWN: 2,
        CheckState.BLOCKED: 3,
    }[state]


def _sorted_findings(findings) -> tuple[BootstrapFinding, ...]:
    return tuple(
        sorted(
            findings,
            key=lambda item: (item.code.casefold(), item.state.value, item.message),
        )
    )


def _require_direct_workspace(root: Path) -> None:
    for candidate in (root, *root.parents):
        try:
            metadata = candidate.lstat()
        except OSError as error:
            raise Mo2BootstrapRefusal(
                "plan-invalid", f"workspace path is missing or unreadable: {candidate}"
            ) from error
        if _redirected(candidate, metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise Mo2BootstrapRefusal(
                "plan-invalid", f"workspace path is redirected or not a directory: {candidate}"
            )
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"workspace root cannot be resolved: {error}"
        ) from error
    if resolved != root:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"workspace root is redirected: {root}"
        )


def _require_direct_workspace_branches(layout: WorkspaceLayout) -> None:
    for branch in (
        layout.metadata,
        layout.archives,
        layout.skyrim_environment_configuration.parent,
        layout.mo2_bootstrap_jobs,
        layout.mo2_bootstrap_plans,
        layout.mo2_bootstrap_receipts,
        layout.skyrim_mo2.parent,
    ):
        _require_direct_existing_chain(layout.root, branch)


def _require_direct_existing_chain(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise Mo2BootstrapRefusal(
            "plan-invalid", f"workspace branch escapes its root: {target}"
        ) from error
    current = root
    for part in relative.parts:
        current = current / part
        metadata = _lstat_if_exists(current)
        if metadata is None:
            continue
        if _redirected(current, metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise Mo2BootstrapRefusal(
                "plan-invalid",
                f"workspace branch is redirected or not a directory: {current}",
            )


def _require_direct_regular_file(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise OSError(f"{label} is missing or unreadable: {path}") from error
    if _redirected(path, metadata) or not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"{label} is not a direct regular file: {path}")


def _require_direct_file_under(root: Path, path: Path, label: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise OSError(f"{label} escapes its trusted root: {path}") from error
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        metadata = current.lstat()
        if _redirected(current, metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"{label} has a redirected or invalid ancestor: {current}")
    _require_direct_regular_file(path, label)


def _stable_direct_file_bytes(path: Path, label: str) -> bytes:
    metadata = path.lstat()
    if _redirected(path, metadata) or not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"{label} is not a direct regular file: {path}")
    first = path.read_bytes()
    second = path.read_bytes()
    if first != second:
        raise OSError(f"{label} changed while reading: {path}")
    return first


def _stable_direct_file_hash(path: Path, label: str) -> tuple[str, int]:
    first = _hash_direct_file(path, label)
    second = _hash_direct_file(path, label)
    if first != second:
        raise OSError(f"{label} changed while hashing: {path}")
    return first


def _hash_direct_file(path: Path, label: str) -> tuple[str, int]:
    metadata = path.lstat()
    if _redirected(path, metadata) or not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"{label} is not a direct regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str):
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _same_path(left: str | Path, right: str | Path) -> bool:
    return str(PureWindowsPath(left)).casefold() == str(PureWindowsPath(right)).casefold()


def _lstat_if_exists(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _redirected(path: Path, metadata) -> bool:
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


__all__ = [
    "BOOTSTRAP_FINDING_CODES",
    "Mo2BootstrapError",
    "Mo2BootstrapRefusal",
    "apply_mo2_setup",
    "prepare_mo2_setup",
]
