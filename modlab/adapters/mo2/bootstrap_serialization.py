"""Canonical, strict serialization for verified MO2 bootstrap records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from modlab.recipes.model import CheckState
from modlab.adapters.skyrim.primary_plugins import (
    SkyrimPrimaryPluginError,
    validate_skyrim_plugin_name,
)

from .bootstrap_model import (
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
    OptionalFileIdentity,
    ProcessIdentity,
    ProcessObservation,
    ProfileSeedEvidence,
    TargetSnapshot,
)
from .path_budget import PathBudgetError, budget_from_dict, budget_to_dict, stage_name


class BootstrapFormatError(ValueError):
    """Raised when bootstrap evidence is unsafe or internally inconsistent."""


_PLAN_BODY_FIELDS = {
    "schemaVersion",
    "disposition",
    "workspaceRoot",
    "steamRoot",
    "gameRoot",
    "finalRoot",
    "stagingParent",
    "stagingNameTemplate",
    "skyrimExecutable",
    "environmentSha256",
    "baselineId",
    "baselineStatus",
    "releaseDescriptorPath",
    "releaseDescriptorSha256",
    "releaseId",
    "archive",
    "extractor",
    "target",
    "processes",
    "profileSeed",
    "writeTemplates",
    "findings",
    "exclusions",
    "coverage",
    "downloads",
    "installations",
    "managerChanges",
    "gameChanges",
    "programsLaunched",
}
_PLAN_FIELDS = _PLAN_BODY_FIELDS | {"planId"}
_JOURNAL_FIELDS = {
    "schemaVersion",
    "jobId",
    "planId",
    "disposition",
    "state",
    "stageRoot",
    "priorRoot",
    "finalRoot",
    "priorTargetKind",
    "priorInventorySha256",
    "priorEntryCount",
    "stageInventorySha256",
    "stageEntryCount",
    "activatedInventorySha256",
    "activatedEntryCount",
    "createdAt",
    "updatedAt",
    "receiptId",
    "error",
}
_RECEIPT_BODY_FIELDS = {
    "schemaVersion",
    "qualification",
    "mode",
    "planId",
    "jobId",
    "releaseId",
    "releaseDescriptorSha256",
    "archiveArtifactId",
    "archiveMetadataSha256",
    "archiveSha256",
    "archiveSize",
    "skyrimExecutable",
    "mo2Executable",
    "finalRoot",
    "downloadsRoot",
    "modsRoot",
    "profilesRoot",
    "overwriteRoot",
    "webcacheRoot",
    "packageInventorySha256",
    "packageFileCount",
    "packageSize",
    "mutablePackagePaths",
    "extraEntries",
    "profileStateSha256",
    "extractor",
    "verifiedAt",
    "findings",
    "coverage",
    "repairable",
    "upgradeSupported",
}
_RECEIPT_FIELDS = _RECEIPT_BODY_FIELDS | {"receiptId"}
_FILE_FIELDS = {"path", "sha256", "size"}
_OPTIONAL_FILE_FIELDS = {"path", "present", "sha256", "size"}
_EXTRACTOR_FIELDS = {"executable", "version"}
_ARCHIVE_FIELDS = {
    "artifactId",
    "metadataSha256",
    "originalName",
    "storedPath",
    "sha256",
    "size",
    "entryCount",
    "listingSha256",
}
_TARGET_FIELDS = {"kind", "root", "inventorySha256", "entryCount"}
_PROCESS_FIELDS = {"pid", "imageName", "executablePath"}
_PROCESS_OBSERVATION_FIELDS = {"complete", "relevant", "error"}
_PROFILE_SEED_FIELDS = {
    "documentsRoot",
    "primaryPlugins",
    "skyrimCcc",
    "iniSources",
}
_FINDING_FIELDS = {"state", "code", "message"}
_COVERAGE_FIELDS = {"key", "value"}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLAN_ID = re.compile(r"^bootstrap-plan-sha256:[0-9a-f]{64}$")
_RECEIPT_ID = re.compile(r"^bootstrap-receipt-sha256:[0-9a-f]{64}$")
_JOB_ID = re.compile(r"^bootstrap-job:([0-9a-f]{32})$")
_ARTIFACT_ID = re.compile(r"^archive-sha256:([0-9a-f]{64})$")
_CHECKPOINT_ID = re.compile(r"^checkpoint-sha256:[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_TEMPLATE_TOKEN = re.compile(r"\{([^{}]+)\}")
_PROFILE_INI_NAMES = ("Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini")
_ALLOWED_TEMPLATE_TOKENS = {"planId", "jobId", "receiptId"}
_BASELINE_STATUSES = {"Matched", "NoBaseline", "Drifted", "Blocked"}
_TARGET_KINDS = {"Empty", "Existing", "Blocked"}
_EXPECTED_COVERAGE = (
    CoverageEntry("installedModPayloads", "NotLinked"),
    CoverageEntry("managerArtifact", "VerifiedPackageSubset"),
    CoverageEntry("mutableManagerState", "ExcludedRecorded"),
    CoverageEntry("runtimeValidation", "NotPerformed"),
    CoverageEntry("smokeTest", "NotPerformed"),
)
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def plan_id_for(value: BootstrapPlan) -> str:
    normalized = _parse_plan_body(_plan_body_to_dict(value))
    digest = hashlib.sha256(_canonical_bytes(_plan_body_to_dict(normalized))).hexdigest()
    return f"bootstrap-plan-sha256:{digest}"


def plan_to_dict(value: BootstrapPlan) -> dict[str, object]:
    normalized = _parse_plan_body(_plan_body_to_dict(value))
    expected = _plan_id_for_normalized(normalized)
    plan_id = _typed_id(value.plan_id, _PLAN_ID, "planId")
    if plan_id != expected:
        raise BootstrapFormatError(f"planId does not match canonical content; expected {expected}")
    result = _plan_body_to_dict(normalized)
    result["planId"] = plan_id
    return result


def plan_from_dict(value: object) -> BootstrapPlan:
    data = _exact_mapping(value, _versioned_fields(value, _PLAN_FIELDS), "bootstrap plan")
    plan_id = _typed_id(data["planId"], _PLAN_ID, "planId")
    body = {key: data[key] for key in _versioned_fields(data, _PLAN_BODY_FIELDS)}
    parsed = _parse_plan_body(body)
    expected = _plan_id_for_normalized(parsed)
    if plan_id != expected:
        raise BootstrapFormatError(f"planId does not match canonical content; expected {expected}")
    return replace(parsed, plan_id=plan_id)


def plan_to_bytes(value: BootstrapPlan) -> bytes:
    return _canonical_bytes(plan_to_dict(value))


def plan_from_bytes(data: bytes) -> BootstrapPlan:
    return plan_from_dict(_decode_document(data, "bootstrap plan"))


def journal_to_dict(value: BootstrapJournal) -> dict[str, object]:
    normalized = journal_from_dict(_journal_to_dict(value))
    return _journal_to_dict(normalized)


def journal_from_dict(value: object) -> BootstrapJournal:
    data = _exact_mapping(value, _versioned_fields(value, _JOURNAL_FIELDS), "bootstrap journal")
    schema_version = _layout_schema_version(data["schemaVersion"])
    job_id = _typed_id(data["jobId"], _JOB_ID, "jobId")
    disposition = _enum(BootstrapDisposition, data["disposition"], "disposition")
    if disposition not in {BootstrapDisposition.CREATE, BootstrapDisposition.ADOPT}:
        raise BootstrapFormatError("journal disposition must be Create or Adopt")
    state = _enum(BootstrapJobState, data["state"], "state")
    stage_root = _absolute_path(data["stageRoot"], "stageRoot")
    prior_root = _absolute_path(data["priorRoot"], "priorRoot")
    final_root = _absolute_path(data["finalRoot"], "finalRoot")
    if len({stage_root.casefold(), prior_root.casefold(), final_root.casefold()}) != 3:
        raise BootstrapFormatError("stageRoot, priorRoot, and finalRoot must differ")
    match = _JOB_ID.fullmatch(job_id)
    assert match is not None
    expected_stage_name = stage_name(job_id, schema_version)
    stage_path = PureWindowsPath(stage_root)
    final_path = PureWindowsPath(final_root)
    if (
        stage_path.name.casefold() != expected_stage_name.casefold()
        or stage_path.parent != final_path.parent
    ):
        raise BootstrapFormatError("stageRoot must use the job-derived sibling name")

    prior_kind = _one_of(data["priorTargetKind"], _TARGET_KINDS, "priorTargetKind")
    expected_prior_kind = (
        "Empty"
        if disposition is BootstrapDisposition.CREATE
        else "Existing"
    )
    if prior_kind != expected_prior_kind:
        raise BootstrapFormatError(
            "journal priorTargetKind must be "
            f"{expected_prior_kind} for {disposition.value}"
        )
    stage_sha, stage_count = _optional_inventory_pair(
        data["stageInventorySha256"], data["stageEntryCount"], "stage"
    )
    activated_sha, activated_count = _optional_inventory_pair(
        data["activatedInventorySha256"],
        data["activatedEntryCount"],
        "activated",
    )
    receipt_id = _optional_typed_id(data["receiptId"], _RECEIPT_ID, "receiptId")
    error = _optional_text(data["error"], "error")
    created_at = _utc_timestamp(data["createdAt"], "createdAt")
    updated_at = _utc_timestamp(data["updatedAt"], "updatedAt")
    if _timestamp_value(updated_at) < _timestamp_value(created_at):
        raise BootstrapFormatError("updatedAt must not precede createdAt")

    if state in {BootstrapJobState.STAGED, BootstrapJobState.APPLYING} and stage_sha is None:
        raise BootstrapFormatError(f"{state.value} journal requires staged inventory")
    if state in {BootstrapJobState.ACTIVATED, BootstrapJobState.VERIFIED}:
        if stage_sha is None:
            raise BootstrapFormatError(f"{state.value} journal requires staged inventory")
        if disposition is BootstrapDisposition.CREATE and activated_sha is None:
            raise BootstrapFormatError(f"{state.value} Create journal requires activated inventory")
    if state is BootstrapJobState.VERIFIED and receipt_id is None:
        raise BootstrapFormatError("Verified journal requires receiptId")
    if state is not BootstrapJobState.VERIFIED and receipt_id is not None:
        raise BootstrapFormatError(f"{state.value} journal cannot record receiptId")
    if state is BootstrapJobState.RECOVERY_REQUIRED:
        if error is None:
            raise BootstrapFormatError("RecoveryRequired journal requires error")
    elif error is not None:
        raise BootstrapFormatError(f"{state.value} journal cannot record error")

    return BootstrapJournal(
        schema_version=schema_version,
        job_id=job_id,
        plan_id=_typed_id(data["planId"], _PLAN_ID, "planId"),
        disposition=disposition,
        state=state,
        stage_root=stage_root,
        prior_root=prior_root,
        final_root=final_root,
        prior_target_kind=prior_kind,
        prior_inventory_sha256=_sha256(data["priorInventorySha256"], "priorInventorySha256"),
        prior_entry_count=_nonnegative_integer(data["priorEntryCount"], "priorEntryCount"),
        stage_inventory_sha256=stage_sha,
        stage_entry_count=stage_count,
        activated_inventory_sha256=activated_sha,
        activated_entry_count=activated_count,
        created_at=created_at,
        updated_at=updated_at,
        receipt_id=receipt_id,
        error=error,
        path_budget=_path_budget_from_document(data),
    )


def journal_to_bytes(value: BootstrapJournal) -> bytes:
    return _canonical_bytes(journal_to_dict(value))


def journal_from_bytes(data: bytes) -> BootstrapJournal:
    return journal_from_dict(_decode_document(data, "bootstrap journal"))


def receipt_id_for(value: BootstrapReceipt) -> str:
    normalized = _parse_receipt_body(_receipt_body_to_dict(value))
    digest = hashlib.sha256(
        _canonical_bytes(_receipt_body_to_dict(normalized))
    ).hexdigest()
    return f"bootstrap-receipt-sha256:{digest}"


def receipt_to_dict(value: BootstrapReceipt) -> dict[str, object]:
    normalized = _parse_receipt_body(_receipt_body_to_dict(value))
    expected = _receipt_id_for_normalized(normalized)
    receipt_id = _typed_id(value.receipt_id, _RECEIPT_ID, "receiptId")
    if receipt_id != expected:
        raise BootstrapFormatError(
            f"receiptId does not match canonical content; expected {expected}"
        )
    result = _receipt_body_to_dict(normalized)
    result["receiptId"] = receipt_id
    return result


def receipt_from_dict(value: object) -> BootstrapReceipt:
    data = _exact_mapping(value, _RECEIPT_FIELDS, "bootstrap receipt")
    receipt_id = _typed_id(data["receiptId"], _RECEIPT_ID, "receiptId")
    body = {key: data[key] for key in _RECEIPT_BODY_FIELDS}
    parsed = _parse_receipt_body(body)
    expected = _receipt_id_for_normalized(parsed)
    if receipt_id != expected:
        raise BootstrapFormatError(
            f"receiptId does not match canonical content; expected {expected}"
        )
    return replace(parsed, receipt_id=receipt_id)


def receipt_to_bytes(value: BootstrapReceipt) -> bytes:
    return _canonical_bytes(receipt_to_dict(value))


def receipt_from_bytes(data: bytes) -> BootstrapReceipt:
    return receipt_from_dict(_decode_document(data, "bootstrap receipt"))


def _plan_id_for_normalized(value: BootstrapPlan) -> str:
    digest = hashlib.sha256(_canonical_bytes(_plan_body_to_dict(value))).hexdigest()
    return f"bootstrap-plan-sha256:{digest}"


def _receipt_id_for_normalized(value: BootstrapReceipt) -> str:
    digest = hashlib.sha256(
        _canonical_bytes(_receipt_body_to_dict(value))
    ).hexdigest()
    return f"bootstrap-receipt-sha256:{digest}"


def _parse_plan_body(value: object) -> BootstrapPlan:
    data = _exact_mapping(value, _versioned_fields(value, _PLAN_BODY_FIELDS), "bootstrap plan body")
    version = _layout_schema_version(data["schemaVersion"])
    disposition = _enum(BootstrapDisposition, data["disposition"], "disposition")
    environment_sha = _optional_sha256(data["environmentSha256"], "environmentSha256")
    baseline_id = _optional_typed_id(data["baselineId"], _CHECKPOINT_ID, "baselineId")
    baseline_status = _one_of(data["baselineStatus"], _BASELINE_STATUSES, "baselineStatus")
    if baseline_status == "NoBaseline" and baseline_id is not None:
        raise BootstrapFormatError("NoBaseline plan cannot record baselineId")
    if baseline_status != "NoBaseline" and baseline_id is None:
        raise BootstrapFormatError(f"{baseline_status} plan requires baselineId")
    if environment_sha is None and baseline_id is not None:
        raise BootstrapFormatError("baselineId requires environmentSha256")

    downloads = _text_array(data["downloads"], "downloads")
    installations = _text_array(data["installations"], "installations")
    manager_changes = _text_array(data["managerChanges"], "managerChanges")
    game_changes = _text_array(data["gameChanges"], "gameChanges")
    if downloads or installations or manager_changes or game_changes:
        raise BootstrapFormatError("bootstrap preview action arrays must be empty")

    return BootstrapPlan(
        schema_version=version,
        plan_id="",
        disposition=disposition,
        workspace_root=_absolute_path(data["workspaceRoot"], "workspaceRoot"),
        steam_root=_absolute_path(data["steamRoot"], "steamRoot"),
        game_root=_absolute_path(data["gameRoot"], "gameRoot"),
        final_root=_absolute_path(data["finalRoot"], "finalRoot"),
        staging_parent=_absolute_path(data["stagingParent"], "stagingParent"),
        staging_name_template=_fixed_text(
            data["stagingNameTemplate"],
            ".skyrim-se-ae.modlab-stage-<job-hex>" if version == 1 else ".s<job-base32>",
            "stagingNameTemplate",
        ),
        skyrim_executable=_file_from_dict(data["skyrimExecutable"], "skyrimExecutable"),
        environment_sha256=environment_sha,
        baseline_id=baseline_id,
        baseline_status=baseline_status,
        release_descriptor_path=_relative_path(
            data["releaseDescriptorPath"], "releaseDescriptorPath"
        ),
        release_descriptor_sha256=_sha256(
            data["releaseDescriptorSha256"], "releaseDescriptorSha256"
        ),
        release_id=_fixed_text(
            data["releaseId"], "mo2.windows-portable.2.5.2", "releaseId"
        ),
        archive=_archive_from_dict(data["archive"]),
        extractor=_extractor_from_dict(data["extractor"]),
        target=_target_from_dict(data["target"]),
        processes=_process_observation_from_dict(data["processes"]),
        profile_seed=_profile_seed_from_dict(data["profileSeed"]),
        write_templates=_template_array(data["writeTemplates"], "writeTemplates"),
        findings=_finding_array(data["findings"]),
        exclusions=_sorted_text_array(data["exclusions"], "exclusions"),
        coverage=_coverage_array(data["coverage"]),
        downloads=downloads,
        installations=installations,
        manager_changes=manager_changes,
        game_changes=game_changes,
        programs_launched=_text_array(data["programsLaunched"], "programsLaunched"),
        path_budget=_path_budget_from_document(data),
    )


def _parse_receipt_body(value: object) -> BootstrapReceipt:
    data = _exact_mapping(value, _RECEIPT_BODY_FIELDS, "bootstrap receipt body")
    archive_sha = _sha256(data["archiveSha256"], "archiveSha256")
    artifact_id = _typed_id(data["archiveArtifactId"], _ARTIFACT_ID, "archiveArtifactId")
    artifact_match = _ARTIFACT_ID.fullmatch(artifact_id)
    assert artifact_match is not None
    if artifact_match.group(1) != archive_sha:
        raise BootstrapFormatError("archiveArtifactId must match archiveSha256")

    final_root = _absolute_path(data["finalRoot"], "finalRoot")
    final = PureWindowsPath(final_root)
    contained_roots = {
        "downloadsRoot": "downloads",
        "modsRoot": "mods",
        "profilesRoot": "profiles",
        "overwriteRoot": "overwrite",
        "webcacheRoot": "webcache",
    }
    parsed_roots: dict[str, str] = {}
    for field, child in contained_roots.items():
        parsed = _absolute_path(data[field], field)
        if PureWindowsPath(parsed) != final / child:
            raise BootstrapFormatError(f"{field} must be the fixed child of finalRoot")
        parsed_roots[field] = parsed

    mo2_executable = _file_from_dict(data["mo2Executable"], "mo2Executable")
    if PureWindowsPath(mo2_executable.path) != final / "app" / "ModOrganizer.exe":
        raise BootstrapFormatError("mo2Executable.path must be finalRoot/app/ModOrganizer.exe")
    repairable = _boolean(data["repairable"], "repairable")
    upgrade_supported = _boolean(data["upgradeSupported"], "upgradeSupported")
    if repairable or upgrade_supported:
        raise BootstrapFormatError("repairable and upgradeSupported must be false")

    return BootstrapReceipt(
        schema_version=_schema_version(data["schemaVersion"]),
        receipt_id="",
        qualification=_fixed_text(
            data["qualification"], "VerifiedBootstrap", "qualification"
        ),
        mode=_enum(BootstrapReceiptMode, data["mode"], "mode"),
        plan_id=_typed_id(data["planId"], _PLAN_ID, "planId"),
        job_id=_typed_id(data["jobId"], _JOB_ID, "jobId"),
        release_id=_fixed_text(
            data["releaseId"], "mo2.windows-portable.2.5.2", "releaseId"
        ),
        release_descriptor_sha256=_sha256(
            data["releaseDescriptorSha256"], "releaseDescriptorSha256"
        ),
        archive_artifact_id=artifact_id,
        archive_metadata_sha256=_sha256(
            data["archiveMetadataSha256"], "archiveMetadataSha256"
        ),
        archive_sha256=archive_sha,
        archive_size=_positive_integer(data["archiveSize"], "archiveSize"),
        skyrim_executable=_file_from_dict(data["skyrimExecutable"], "skyrimExecutable"),
        mo2_executable=mo2_executable,
        final_root=final_root,
        downloads_root=parsed_roots["downloadsRoot"],
        mods_root=parsed_roots["modsRoot"],
        profiles_root=parsed_roots["profilesRoot"],
        overwrite_root=parsed_roots["overwriteRoot"],
        webcache_root=parsed_roots["webcacheRoot"],
        package_inventory_sha256=_sha256(
            data["packageInventorySha256"], "packageInventorySha256"
        ),
        package_file_count=_positive_integer(
            data["packageFileCount"], "packageFileCount"
        ),
        package_size=_positive_integer(data["packageSize"], "packageSize"),
        mutable_package_paths=_relative_path_array(
            data["mutablePackagePaths"], "mutablePackagePaths"
        ),
        extra_entries=_relative_path_array(data["extraEntries"], "extraEntries"),
        profile_state_sha256=_sha256(
            data["profileStateSha256"], "profileStateSha256"
        ),
        extractor=_extractor_from_dict(data["extractor"]),
        verified_at=_utc_timestamp(data["verifiedAt"], "verifiedAt"),
        findings=_finding_array(data["findings"]),
        coverage=_coverage_array(data["coverage"]),
        repairable=repairable,
        upgrade_supported=upgrade_supported,
    )


def _plan_body_to_dict(value: BootstrapPlan) -> dict[str, object]:
    return {
        **_path_budget_to_document(value),
        "schemaVersion": value.schema_version,
        "disposition": value.disposition.value,
        "workspaceRoot": value.workspace_root,
        "steamRoot": value.steam_root,
        "gameRoot": value.game_root,
        "finalRoot": value.final_root,
        "stagingParent": value.staging_parent,
        "stagingNameTemplate": value.staging_name_template,
        "skyrimExecutable": _file_to_dict(value.skyrim_executable),
        "environmentSha256": value.environment_sha256,
        "baselineId": value.baseline_id,
        "baselineStatus": value.baseline_status,
        "releaseDescriptorPath": value.release_descriptor_path,
        "releaseDescriptorSha256": value.release_descriptor_sha256,
        "releaseId": value.release_id,
        "archive": _archive_to_dict(value.archive),
        "extractor": _extractor_to_dict(value.extractor),
        "target": _target_to_dict(value.target),
        "processes": _process_observation_to_dict(value.processes),
        "profileSeed": _profile_seed_to_dict(value.profile_seed),
        "writeTemplates": list(value.write_templates),
        "findings": [_finding_to_dict(item) for item in value.findings],
        "exclusions": list(value.exclusions),
        "coverage": [_coverage_to_dict(item) for item in value.coverage],
        "downloads": list(value.downloads),
        "installations": list(value.installations),
        "managerChanges": list(value.manager_changes),
        "gameChanges": list(value.game_changes),
        "programsLaunched": list(value.programs_launched),
    }


def _journal_to_dict(value: BootstrapJournal) -> dict[str, object]:
    return {
        **_path_budget_to_document(value),
        "schemaVersion": value.schema_version,
        "jobId": value.job_id,
        "planId": value.plan_id,
        "disposition": value.disposition.value,
        "state": value.state.value,
        "stageRoot": value.stage_root,
        "priorRoot": value.prior_root,
        "finalRoot": value.final_root,
        "priorTargetKind": value.prior_target_kind,
        "priorInventorySha256": value.prior_inventory_sha256,
        "priorEntryCount": value.prior_entry_count,
        "stageInventorySha256": value.stage_inventory_sha256,
        "stageEntryCount": value.stage_entry_count,
        "activatedInventorySha256": value.activated_inventory_sha256,
        "activatedEntryCount": value.activated_entry_count,
        "createdAt": value.created_at,
        "updatedAt": value.updated_at,
        "receiptId": value.receipt_id,
        "error": value.error,
    }


def _receipt_body_to_dict(value: BootstrapReceipt) -> dict[str, object]:
    return {
        "schemaVersion": value.schema_version,
        "qualification": value.qualification,
        "mode": value.mode.value,
        "planId": value.plan_id,
        "jobId": value.job_id,
        "releaseId": value.release_id,
        "releaseDescriptorSha256": value.release_descriptor_sha256,
        "archiveArtifactId": value.archive_artifact_id,
        "archiveMetadataSha256": value.archive_metadata_sha256,
        "archiveSha256": value.archive_sha256,
        "archiveSize": value.archive_size,
        "skyrimExecutable": _file_to_dict(value.skyrim_executable),
        "mo2Executable": _file_to_dict(value.mo2_executable),
        "finalRoot": value.final_root,
        "downloadsRoot": value.downloads_root,
        "modsRoot": value.mods_root,
        "profilesRoot": value.profiles_root,
        "overwriteRoot": value.overwrite_root,
        "webcacheRoot": value.webcache_root,
        "packageInventorySha256": value.package_inventory_sha256,
        "packageFileCount": value.package_file_count,
        "packageSize": value.package_size,
        "mutablePackagePaths": list(value.mutable_package_paths),
        "extraEntries": list(value.extra_entries),
        "profileStateSha256": value.profile_state_sha256,
        "extractor": _extractor_to_dict(value.extractor),
        "verifiedAt": value.verified_at,
        "findings": [_finding_to_dict(item) for item in value.findings],
        "coverage": [_coverage_to_dict(item) for item in value.coverage],
        "repairable": value.repairable,
        "upgradeSupported": value.upgrade_supported,
    }


def _file_to_dict(value: FileIdentity) -> dict[str, object]:
    return {"path": value.path, "sha256": value.sha256, "size": value.size}


def _file_from_dict(value: object, label: str) -> FileIdentity:
    data = _exact_mapping(value, _FILE_FIELDS, label)
    return FileIdentity(
        path=_absolute_path(data["path"], f"{label}.path"),
        sha256=_sha256(data["sha256"], f"{label}.sha256"),
        size=_positive_integer(data["size"], f"{label}.size"),
    )


def _optional_file_to_dict(value: OptionalFileIdentity) -> dict[str, object]:
    return {
        "path": value.path,
        "present": value.present,
        "sha256": value.sha256,
        "size": value.size,
    }


def _optional_file_from_dict(value: object, label: str) -> OptionalFileIdentity:
    data = _exact_mapping(value, _OPTIONAL_FILE_FIELDS, label)
    present = _boolean(data["present"], f"{label}.present")
    sha = data["sha256"]
    size = data["size"]
    if present:
        parsed_sha = _sha256(sha, f"{label}.sha256")
        parsed_size = _nonnegative_integer(size, f"{label}.size")
    else:
        if sha is not None or size is not None:
            raise BootstrapFormatError(f"absent {label} must have null sha256 and size")
        parsed_sha = None
        parsed_size = None
    return OptionalFileIdentity(
        path=_absolute_path(data["path"], f"{label}.path"),
        present=present,
        sha256=parsed_sha,
        size=parsed_size,
    )


def _extractor_to_dict(value: ExtractorIdentity) -> dict[str, object]:
    return {"executable": _file_to_dict(value.executable), "version": value.version}


def _extractor_from_dict(value: object) -> ExtractorIdentity:
    data = _exact_mapping(value, _EXTRACTOR_FIELDS, "extractor")
    executable = _file_from_dict(data["executable"], "extractor.executable")
    if PureWindowsPath(executable.path) != PureWindowsPath(r"C:\Windows\System32\tar.exe"):
        raise BootstrapFormatError("extractor executable must be C:\\Windows\\System32\\tar.exe")
    return ExtractorIdentity(
        executable=executable,
        version=_text(data["version"], "extractor.version"),
    )


def _archive_to_dict(value: ArchiveEvidence) -> dict[str, object]:
    return {
        "artifactId": value.artifact_id,
        "metadataSha256": value.metadata_sha256,
        "originalName": value.original_name,
        "storedPath": value.stored_path,
        "sha256": value.sha256,
        "size": value.size,
        "entryCount": value.entry_count,
        "listingSha256": value.listing_sha256,
    }


def _archive_from_dict(value: object) -> ArchiveEvidence:
    data = _exact_mapping(value, _ARCHIVE_FIELDS, "archive")
    sha = _sha256(data["sha256"], "archive.sha256")
    artifact_id = _typed_id(data["artifactId"], _ARTIFACT_ID, "archive.artifactId")
    match = _ARTIFACT_ID.fullmatch(artifact_id)
    assert match is not None
    if match.group(1) != sha:
        raise BootstrapFormatError("archive.artifactId must match archive.sha256")
    original_name = _text(data["originalName"], "archive.originalName")
    if any(separator in original_name for separator in ("/", "\\")):
        raise BootstrapFormatError("archive.originalName must be one file name")
    return ArchiveEvidence(
        artifact_id=artifact_id,
        metadata_sha256=_sha256(data["metadataSha256"], "archive.metadataSha256"),
        original_name=original_name,
        stored_path=_relative_path(data["storedPath"], "archive.storedPath"),
        sha256=sha,
        size=_positive_integer(data["size"], "archive.size"),
        entry_count=_positive_integer(data["entryCount"], "archive.entryCount"),
        listing_sha256=_sha256(data["listingSha256"], "archive.listingSha256"),
    )


def _target_to_dict(value: TargetSnapshot) -> dict[str, object]:
    return {
        "kind": value.kind,
        "root": value.root,
        "inventorySha256": value.inventory_sha256,
        "entryCount": value.entry_count,
    }


def _target_from_dict(value: object) -> TargetSnapshot:
    data = _exact_mapping(value, _TARGET_FIELDS, "target")
    return TargetSnapshot(
        kind=_one_of(data["kind"], _TARGET_KINDS, "target.kind"),
        root=_absolute_path(data["root"], "target.root"),
        inventory_sha256=_sha256(data["inventorySha256"], "target.inventorySha256"),
        entry_count=_nonnegative_integer(data["entryCount"], "target.entryCount"),
    )


def _process_to_dict(value: ProcessIdentity) -> dict[str, object]:
    return {
        "pid": value.pid,
        "imageName": value.image_name,
        "executablePath": value.executable_path,
    }


def _process_from_dict(value: object, index: int) -> ProcessIdentity:
    label = f"processes.relevant[{index}]"
    data = _exact_mapping(value, _PROCESS_FIELDS, label)
    image_name = _text(data["imageName"], f"{label}.imageName")
    if any(separator in image_name for separator in ("/", "\\")):
        raise BootstrapFormatError(f"{label}.imageName must be one file name")
    return ProcessIdentity(
        pid=_positive_integer(data["pid"], f"{label}.pid"),
        image_name=image_name,
        executable_path=_absolute_path(data["executablePath"], f"{label}.executablePath"),
    )


def _process_observation_to_dict(value: ProcessObservation) -> dict[str, object]:
    return {
        "complete": value.complete,
        "relevant": [_process_to_dict(item) for item in value.relevant],
        "error": value.error,
    }


def _process_observation_from_dict(value: object) -> ProcessObservation:
    data = _exact_mapping(value, _PROCESS_OBSERVATION_FIELDS, "processes")
    complete = _boolean(data["complete"], "processes.complete")
    relevant_value = data["relevant"]
    if not isinstance(relevant_value, list):
        raise BootstrapFormatError("processes.relevant must be an array")
    relevant = tuple(
        _process_from_dict(item, index) for index, item in enumerate(relevant_value)
    )
    pids = tuple(item.pid for item in relevant)
    if pids != tuple(sorted(pids)) or len(pids) != len(set(pids)):
        raise BootstrapFormatError("processes.relevant must have unique sorted PIDs")
    error = _optional_text(data["error"], "processes.error")
    if complete and error is not None:
        raise BootstrapFormatError("complete process observation cannot record error")
    if not complete and (relevant or error is None):
        raise BootstrapFormatError("incomplete process observation requires only an error")
    return ProcessObservation(complete=complete, relevant=relevant, error=error)


def _profile_seed_to_dict(value: ProfileSeedEvidence) -> dict[str, object]:
    return {
        "documentsRoot": value.documents_root,
        "primaryPlugins": list(value.primary_plugins),
        "skyrimCcc": _optional_file_to_dict(value.skyrim_ccc),
        "iniSources": [_optional_file_to_dict(item) for item in value.ini_sources],
    }


def _profile_seed_from_dict(value: object) -> ProfileSeedEvidence:
    data = _exact_mapping(value, _PROFILE_SEED_FIELDS, "profileSeed")
    documents_root = _absolute_path(
        data["documentsRoot"], "profileSeed.documentsRoot"
    )
    plugins_value = data["primaryPlugins"]
    if not isinstance(plugins_value, list) or not plugins_value:
        raise BootstrapFormatError("profileSeed.primaryPlugins must be a non-empty array")
    plugins = tuple(
        _plugin_name(item, f"profileSeed.primaryPlugins[{index}]")
        for index, item in enumerate(plugins_value)
    )
    if len({item.casefold() for item in plugins}) != len(plugins):
        raise BootstrapFormatError("profileSeed.primaryPlugins contains a duplicate")

    ini_value = data["iniSources"]
    if not isinstance(ini_value, list):
        raise BootstrapFormatError("profileSeed.iniSources must be an array")
    ini_sources = tuple(
        _optional_file_from_dict(item, f"profileSeed.iniSources[{index}]")
        for index, item in enumerate(ini_value)
    )
    expected_ini_paths = tuple(
        str(
            PureWindowsPath(
                documents_root,
                "My Games",
                "Skyrim Special Edition",
                name,
            )
        )
        for name in _PROFILE_INI_NAMES
    )
    if tuple(item.path for item in ini_sources) != expected_ini_paths:
        raise BootstrapFormatError(
            "profileSeed.iniSources must contain the exact Skyrim INI paths "
            "in semantic order"
        )
    return ProfileSeedEvidence(
        documents_root=documents_root,
        primary_plugins=plugins,
        skyrim_ccc=_optional_file_from_dict(data["skyrimCcc"], "profileSeed.skyrimCcc"),
        ini_sources=ini_sources,
    )


def _finding_to_dict(value: BootstrapFinding) -> dict[str, object]:
    return {"state": value.state.value, "code": value.code, "message": value.message}


def _finding_array(value: object) -> tuple[BootstrapFinding, ...]:
    if not isinstance(value, list):
        raise BootstrapFormatError("findings must be an array")
    result = tuple(_finding_from_dict(item, index) for index, item in enumerate(value))
    keys = tuple((item.code.casefold(), item.state.value, item.message) for item in result)
    if keys != tuple(sorted(keys)) or len({item.code.casefold() for item in result}) != len(result):
        raise BootstrapFormatError("findings must have unique codes and canonical order")
    return result


def _finding_from_dict(value: object, index: int) -> BootstrapFinding:
    label = f"findings[{index}]"
    data = _exact_mapping(value, _FINDING_FIELDS, label)
    code = _text(data["code"], f"{label}.code")
    if _SAFE_CODE.fullmatch(code) is None:
        raise BootstrapFormatError(f"{label}.code must be lowercase kebab-case")
    return BootstrapFinding(
        state=_enum(CheckState, data["state"], f"{label}.state"),
        code=code,
        message=_text(data["message"], f"{label}.message"),
    )


def _coverage_to_dict(value: CoverageEntry) -> dict[str, object]:
    return {"key": value.key, "value": value.value}


def _coverage_array(value: object) -> tuple[CoverageEntry, ...]:
    if not isinstance(value, list):
        raise BootstrapFormatError("coverage must be an array")
    result: list[CoverageEntry] = []
    for index, item in enumerate(value):
        label = f"coverage[{index}]"
        data = _exact_mapping(item, _COVERAGE_FIELDS, label)
        result.append(
            CoverageEntry(
                key=_text(data["key"], f"{label}.key"),
                value=_text(data["value"], f"{label}.value"),
            )
        )
    normalized = tuple(result)
    if normalized != _EXPECTED_COVERAGE:
        raise BootstrapFormatError("coverage must use the exact bounded bootstrap claims")
    return normalized


def _decode_document(data: bytes, label: str) -> object:
    if type(data) is not bytes:
        raise BootstrapFormatError(f"{label} data must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BootstrapFormatError(f"{label} is not valid UTF-8: {error}") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as error:
        raise BootstrapFormatError(
            f"{label} is not valid JSON: line {error.lineno}, column {error.colno}"
        ) from error


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise BootstrapFormatError(f"non-finite JSON value is not allowed: {value}")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _exact_mapping(
    value: object, expected_fields: set[str], label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BootstrapFormatError(f"{label} must be an object")
    actual = set(value)
    if actual != expected_fields:
        raise BootstrapFormatError(
            f"{label} fields differ: missing={sorted(expected_fields - actual)}, "
            f"extra={sorted(actual - expected_fields)}"
        )
    return value


def _layout_schema_version(value: object) -> int:
    if type(value) is not int or value not in (1, 2):
        raise BootstrapFormatError("unsupported bootstrap layout schemaVersion")
    return value


def _versioned_fields(value, fields):
    if not isinstance(value, Mapping):
        return fields
    version = _layout_schema_version(value.get("schemaVersion"))
    return fields if version == 1 else fields | {"pathBudget"}


def _path_budget_from_document(value):
    if value["schemaVersion"] == 1:
        return None
    try:
        return budget_from_dict(value["pathBudget"])
    except PathBudgetError as error:
        raise BootstrapFormatError(str(error)) from error


def _path_budget_to_document(value):
    if value.schema_version == 1:
        if value.path_budget is not None:
            raise BootstrapFormatError("legacy layout cannot carry a new path budget")
        return {}
    try:
        return {"pathBudget": budget_to_dict(value.path_budget)}
    except PathBudgetError as error:
        raise BootstrapFormatError(str(error)) from error


def _schema_version(value: object) -> int:
    if type(value) is not int or value != 1:
        raise BootstrapFormatError("schemaVersion must be integer 1")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BootstrapFormatError(f"{label} must be non-blank unpadded text")
    if any(ord(character) < 32 for character in value):
        raise BootstrapFormatError(f"{label} must not contain control characters")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _fixed_text(value: object, expected: str, label: str) -> str:
    text = _text(value, label)
    if text != expected:
        raise BootstrapFormatError(f"{label} must equal {expected}")
    return text


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise BootstrapFormatError(f"{label} must be boolean")
    return value


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise BootstrapFormatError(f"{label} must be a positive integer")
    return value


def _nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise BootstrapFormatError(f"{label} must be a non-negative integer")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BootstrapFormatError(f"{label} must be a lowercase SHA-256")
    return value


def _optional_sha256(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, label)


def _typed_id(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise BootstrapFormatError(f"{label} has an invalid typed identity")
    return value


def _optional_typed_id(
    value: object, pattern: re.Pattern[str], label: str
) -> str | None:
    if value is None:
        return None
    return _typed_id(value, pattern, label)


def _enum(enum_type: type[Any], value: object, label: str) -> Any:
    if not isinstance(value, str):
        raise BootstrapFormatError(f"{label} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise BootstrapFormatError(f"{label} must be one of: {allowed}") from error


def _one_of(value: object, choices: set[str], label: str) -> str:
    text = _text(value, label)
    if text not in choices:
        raise BootstrapFormatError(f"{label} must be one of: {', '.join(sorted(choices))}")
    return text


def _utc_timestamp(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BootstrapFormatError(f"{label} must use YYYY-MM-DDTHH:MM:SSZ") from error
    return text


def _timestamp_value(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _absolute_path(value: object, label: str) -> str:
    text = _text(value, label)
    if "/" in text:
        raise BootstrapFormatError(f"{label} must use canonical Windows separators")
    path = PureWindowsPath(text)
    if not path.is_absolute() or not path.drive or str(path) != text:
        raise BootstrapFormatError(f"{label} must be a canonical absolute Windows path")
    _reject_save_parts(path.parts, path.suffix, label)
    for part in path.parts[1:]:
        _validate_windows_component(part, label)
    return text


def _relative_path(value: object, label: str) -> str:
    text = _text(value, label)
    if "\\" in text or text.startswith("/") or PureWindowsPath(text).drive:
        raise BootstrapFormatError(f"{label} must be a safe forward-slash relative path")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BootstrapFormatError(f"{label} must be a safe forward-slash relative path")
    for part in parts:
        _validate_windows_component(part, label)
    path = PurePosixPath(text)
    _reject_save_parts(path.parts, path.suffix, label)
    return path.as_posix()


def _validate_windows_component(part: str, label: str) -> None:
    if part.endswith((" ", ".")) or any(
        ord(character) < 32 or character in '<>:"|?*' for character in part
    ):
        raise BootstrapFormatError(f"{label} contains an unsafe Windows path component")
    if part.split(".", 1)[0].casefold() in _RESERVED_WINDOWS_NAMES:
        raise BootstrapFormatError(f"{label} contains a reserved Windows path component")


def _reject_save_parts(parts: tuple[str, ...], suffix: str, label: str) -> None:
    if any(part.casefold() == "saves" for part in parts) or suffix.casefold() in {
        ".ess",
        ".skse",
    }:
        raise BootstrapFormatError(f"{label} must not reference saves or co-saves")


def _plugin_name(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        return validate_skyrim_plugin_name(text, label=label)
    except SkyrimPrimaryPluginError as error:
        raise BootstrapFormatError(str(error)) from error


def _text_array(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BootstrapFormatError(f"{label} must be an array")
    result = tuple(_text(item, f"{label}[{index}]") for index, item in enumerate(value))
    if len(result) != len(set(result)):
        raise BootstrapFormatError(f"{label} must not contain duplicates")
    return result


def _sorted_text_array(value: object, label: str) -> tuple[str, ...]:
    result = _text_array(value, label)
    _require_sorted_unique(result, label)
    return result


def _relative_path_array(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BootstrapFormatError(f"{label} must be an array")
    result = tuple(
        _relative_path(item, f"{label}[{index}]") for index, item in enumerate(value)
    )
    _require_sorted_unique(result, label)
    return result


def _template_array(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BootstrapFormatError(f"{label} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _text(item, f"{label}[{index}]")
        tokens = _TEMPLATE_TOKEN.findall(text)
        if not tokens or any(token not in _ALLOWED_TEMPLATE_TOKENS for token in tokens):
            raise BootstrapFormatError(
                f"{label}[{index}] must use only planId, jobId, or receiptId tokens"
            )
        if len(tokens) != len(set(tokens)):
            raise BootstrapFormatError(f"{label}[{index}] repeats a template token")
        _relative_path(text, f"{label}[{index}]")
        result.append(text)
    normalized = tuple(result)
    _require_sorted_unique(normalized, label)
    return normalized


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    folded = tuple(item.casefold() for item in values)
    if len(folded) != len(set(folded)):
        raise BootstrapFormatError(f"{label} must not contain case-insensitive duplicates")
    if values != tuple(sorted(values, key=lambda item: (item.casefold(), item))):
        raise BootstrapFormatError(f"{label} must use canonical case-insensitive order")


def _optional_inventory_pair(
    sha_value: object, count_value: object, label: str
) -> tuple[str | None, int | None]:
    if sha_value is None and count_value is None:
        return None, None
    if sha_value is None or count_value is None:
        raise BootstrapFormatError(f"{label} inventory identity must be wholly null or present")
    return (
        _sha256(sha_value, f"{label}InventorySha256"),
        _nonnegative_integer(count_value, f"{label}EntryCount"),
    )
