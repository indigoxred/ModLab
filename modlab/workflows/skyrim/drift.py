"""Pure, bounded comparison of current Skyrim state with an observed baseline."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from enum import StrEnum

from modlab.adapters.mo2.comparison_serialization import (
    Mo2ComparisonFormatError,
    adapter_state_from_dict,
)
from modlab.adapters.mo2.projection import (
    Mo2AdapterState,
    Mo2ProfileState,
    adapter_state_sha256,
    adapter_state_to_dict,
)
from modlab.checkpoints.model import CheckpointDraft, CheckpointRecord
from modlab.checkpoints.serialization import (
    CheckpointFormatError,
    checkpoint_record_from_draft,
)
from modlab.recipes.model import CheckState, RecipeReview

from .configuration import SkyrimEnvironmentConfiguration
from .evidence import (
    BaselineEvidenceFormatError,
    ObservedBaselineEvidence,
    baseline_evidence_from_dict,
)
from .observation import SkyrimLiveObservation


class SkyrimStatusOutcome(StrEnum):
    MATCHED = "Matched"
    DRIFTED = "Drifted"
    NO_BASELINE = "NoBaseline"
    BLOCKED = "Blocked"


DriftScalar = str | bool | int | None | tuple[str, ...]


@dataclass(frozen=True)
class DriftValue:
    field: str
    checkpoint_value: DriftScalar
    current_value: DriftScalar


@dataclass(frozen=True)
class DriftDomain:
    name: str
    changes: tuple[DriftValue, ...]


@dataclass(frozen=True)
class SkyrimDriftReport:
    outcome: SkyrimStatusOutcome
    baseline_checkpoint_id: str | None
    domains: tuple[DriftDomain, ...]
    findings: tuple[str, ...]


def compare_observed_baseline(
    configuration: SkyrimEnvironmentConfiguration,
    checkpoint: CheckpointRecord | None,
    checkpoint_evidence: ObservedBaselineEvidence | None,
    current_observation: SkyrimLiveObservation,
    current_recipe_review: RecipeReview,
) -> SkyrimDriftReport:
    baseline_id = configuration.baseline_checkpoint_id
    if baseline_id is None:
        return SkyrimDriftReport(
            SkyrimStatusOutcome.NO_BASELINE,
            None,
            (),
            ("No observed baseline is selected.",),
        )
    if checkpoint is None or checkpoint_evidence is None:
        return _blocked(baseline_id, "Selected checkpoint is missing or unreadable.")
    if not current_observation.ready:
        return _blocked(
            baseline_id,
            "Current Skyrim/MO2 observation is incomplete or blocked.",
        )
    projection = current_observation.projection
    if (
        projection is None
        or projection.adapter_state is None
        or projection.adapter_state_sha256 is None
        or not current_recipe_review.ready_for_approval
    ):
        return _blocked(
            baseline_id,
            "Current manager state or recipe review is incomplete.",
        )

    try:
        baseline_state, parsed_evidence = _validated_checkpoint_content(
            baseline_id, checkpoint
        )
        if parsed_evidence != checkpoint_evidence:
            raise ValueError(
                "supplied checkpoint evidence differs from checkpoint content"
            )
        current_state = adapter_state_from_dict(
            adapter_state_to_dict(projection.adapter_state)
        )
        if adapter_state_sha256(current_state) != projection.adapter_state_sha256:
            raise ValueError("current adapter state hash is inconsistent")
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        CheckpointFormatError,
        Mo2ComparisonFormatError,
        BaselineEvidenceFormatError,
    ) as error:
        return _blocked(
            baseline_id, f"Required checkpoint or current evidence is invalid: {error}"
        )

    domains = tuple(
        domain
        for domain in (
            _compare_game_identity(checkpoint_evidence, current_observation),
            _compare_manager_identity(checkpoint_evidence, current_observation),
            _compare_profile(
                "lab-profile", baseline_state.lab, current_state.lab
            ),
            _compare_profile(
                "play-profile", baseline_state.play, current_state.play
            ),
            _compare_shared_state(baseline_state, current_state),
            _compare_recipe_intent(
                configuration,
                checkpoint,
                checkpoint_evidence,
                current_recipe_review,
            ),
            _compare_target_environment(
                configuration, checkpoint_evidence, current_observation
            ),
            _compare_coverage(checkpoint_evidence, current_observation),
        )
        if domain.changes
    )
    return SkyrimDriftReport(
        outcome=(
            SkyrimStatusOutcome.DRIFTED
            if domains
            else SkyrimStatusOutcome.MATCHED
        ),
        baseline_checkpoint_id=baseline_id,
        domains=domains,
        findings=(),
    )


def _validated_checkpoint_content(
    expected_id: str, checkpoint: CheckpointRecord
) -> tuple[Mo2AdapterState, ObservedBaselineEvidence]:
    if checkpoint.checkpoint_id != expected_id:
        raise ValueError("selected checkpoint ID does not match configuration")
    draft_values = {
        field.name: getattr(checkpoint, field.name)
        for field in fields(CheckpointDraft)
    }
    normalized = checkpoint_record_from_draft(CheckpointDraft(**draft_values))
    if normalized.checkpoint_id != checkpoint.checkpoint_id:
        raise ValueError("checkpoint content differs from its identity")
    if (
        checkpoint.game != "skyrim-se-ae"
        or checkpoint.adapter_id != "portable-mo2-skyrim"
        or checkpoint.artifact_ids
    ):
        raise ValueError("checkpoint is not an unlinked Skyrim observed baseline")
    state = adapter_state_from_dict(
        json.loads(
            checkpoint.adapter_state_json,
            object_pairs_hook=_unique_json_object,
        )
    )
    evidence = baseline_evidence_from_dict(
        json.loads(
            checkpoint.evidence_json,
            object_pairs_hook=_unique_json_object,
        ),
        adapter_state=state,
    )
    return state, evidence


def _compare_game_identity(
    baseline: ObservedBaselineEvidence,
    current: SkyrimLiveObservation,
) -> DriftDomain:
    before = baseline.game_discovery
    after = current.discovery
    changes = _field_changes(
        {
            "steamRoot": before.steam_root,
            "manifestPath": before.manifest_path,
            "appId": before.app_id,
            "installDirectory": before.install_directory,
            "installStateFlags": before.install_state_flags,
            "gameRoot": before.game_root,
            "executable.fileVersion": _value(before.executable, "file_version"),
            "executable.compatibilityRuntime": _value(
                before.executable, "compatibility_runtime"
            ),
            "executable.size": _value(before.executable, "size"),
            "executable.sha256": _value(before.executable, "sha256"),
            "creationClubPluginCount": before.creation_club_plugin_count,
            "creationClubArchiveCount": before.creation_club_archive_count,
        },
        {
            "steamRoot": after.steam_root,
            "manifestPath": after.manifest_path,
            "appId": after.app_id,
            "installDirectory": after.install_directory,
            "installStateFlags": after.install_state_flags,
            "gameRoot": after.game_root,
            "executable.fileVersion": _value(after.executable, "file_version"),
            "executable.compatibilityRuntime": _value(
                after.executable, "compatibility_runtime"
            ),
            "executable.size": _value(after.executable, "size"),
            "executable.sha256": _value(after.executable, "sha256"),
            "creationClubPluginCount": after.creation_club_plugin_count,
            "creationClubArchiveCount": after.creation_club_archive_count,
        },
    )
    before_data = tuple(
        f"{item.relative_path}|{item.extension}|{item.size}"
        for item in before.data_files
    )
    after_data = tuple(
        f"{item.relative_path}|{item.extension}|{item.size}"
        for item in after.data_files
    )
    changes.extend(_sequence_changes("dataFiles", before_data, after_data))
    return DriftDomain("game-identity", tuple(sorted(changes, key=lambda item: item.field)))


def _compare_manager_identity(
    baseline: ObservedBaselineEvidence,
    current: SkyrimLiveObservation,
) -> DriftDomain:
    before = baseline.manager_observation_context
    after = current.projection.observation_context
    changes = _field_changes(
        {
            "mo2Root": before.mo2_root,
            "gameRoot": before.game_root,
            "activeProfile": before.active_profile,
            "executable.fileVersion": _value(before.executable, "file_version"),
            "executable.size": _value(before.executable, "size"),
            "executable.sha256": _value(before.executable, "sha256"),
            "readSetSha256": before.read_set_sha256,
            "readSetStable": before.read_set_stable,
        },
        {
            "mo2Root": after.mo2_root,
            "gameRoot": after.game_root,
            "activeProfile": after.active_profile,
            "executable.fileVersion": _value(after.executable, "file_version"),
            "executable.size": _value(after.executable, "size"),
            "executable.sha256": _value(after.executable, "sha256"),
            "readSetSha256": after.read_set_sha256,
            "readSetStable": after.read_set_stable,
        },
    )
    before_paths = {item.kind: item for item in before.configured_paths}
    after_paths = {item.kind: item for item in after.configured_paths}
    for kind in sorted(set(before_paths) | set(after_paths)):
        left = before_paths.get(kind)
        right = after_paths.get(kind)
        changes.extend(
            _field_changes(
                {
                    f"configuredPaths.{kind}.configuredPath": _value(
                        left, "configured_path"
                    ),
                    f"configuredPaths.{kind}.resolvedPath": _value(
                        left, "resolved_path"
                    ),
                    f"configuredPaths.{kind}.contained": _value(left, "contained"),
                },
                {
                    f"configuredPaths.{kind}.configuredPath": _value(
                        right, "configured_path"
                    ),
                    f"configuredPaths.{kind}.resolvedPath": _value(
                        right, "resolved_path"
                    ),
                    f"configuredPaths.{kind}.contained": _value(right, "contained"),
                },
            )
        )
    return DriftDomain(
        "manager-identity", tuple(sorted(changes, key=lambda item: item.field))
    )


def _compare_profile(
    name: str, before: Mo2ProfileState, after: Mo2ProfileState
) -> DriftDomain:
    changes = _field_changes(
        {
            "profileLocalSaves": before.profile_local_saves,
            "profileLocalSettings": before.profile_local_settings,
        },
        {
            "profileLocalSaves": after.profile_local_saves,
            "profileLocalSettings": after.profile_local_settings,
        },
    )
    changes.extend(
        _named_entry_changes(
            "mods",
            before.mods,
            after.mods,
            lambda item: (
                f"kind={item.kind}|marker={item.marker}|enabled={item.enabled}|"
                f"priority={item.runtime_priority}"
            ),
        )
    )
    changes.extend(
        _named_entry_changes(
            "plugins",
            before.plugins,
            after.plugins,
            lambda item: f"primary={item.primary}|enabled={item.enabled}",
        )
    )
    changes.extend(
        _sequence_changes(
            "effectiveLoadOrder",
            before.effective_load_order,
            after.effective_load_order,
        )
    )
    changes.extend(
        _named_entry_changes(
            "stateFiles",
            before.state_files,
            after.state_files,
            lambda item: f"sha256={item.sha256}|size={item.size}",
            name_getter=lambda item: item.relative_path,
        )
    )
    return DriftDomain(name, tuple(sorted(changes, key=lambda item: item.field)))


def _compare_shared_state(
    before: Mo2AdapterState, after: Mo2AdapterState
) -> DriftDomain:
    changes = _sequence_changes(
        "overwriteEntries", before.overwrite_entries, after.overwrite_entries
    )
    changes.extend(
        _sequence_changes(
            "installedMods",
            tuple(item.name for item in before.installed_mods),
            tuple(item.name for item in after.installed_mods),
        )
    )
    before_meta = {
        item.name: _meta_identity(item.meta_ini) for item in before.installed_mods
    }
    after_meta = {
        item.name: _meta_identity(item.meta_ini) for item in after.installed_mods
    }
    changed_meta = tuple(
        f"{name}: checkpoint={before_meta.get(name)} current={after_meta.get(name)}"
        for name in sorted(set(before_meta) & set(after_meta))
        if before_meta[name] != after_meta[name]
    )
    if changed_meta:
        changes.append(
            DriftValue(
                "installedMods.metaIni",
                _bounded_items(changed_meta),
                _bounded_items(changed_meta),
            )
        )
    return DriftDomain(
        "shared-manager-state",
        tuple(sorted(changes, key=lambda item: item.field)),
    )


def _compare_recipe_intent(
    configuration: SkyrimEnvironmentConfiguration,
    checkpoint: CheckpointRecord,
    baseline: ObservedBaselineEvidence,
    current_review: RecipeReview,
) -> DriftDomain:
    before = {
        "recipeId": checkpoint.recipe_id,
        "revision": checkpoint.recipe_revision,
        "maturity": checkpoint.recipe_maturity.value,
        "identity": baseline.recipe_review.identity.value,
        "sourceSha256": baseline.recipe_review.recipe_source_sha256,
        "selected": baseline.recipe_review.selected,
        "omitted": baseline.recipe_review.omitted,
        "readyForApproval": baseline.recipe_review.ready_for_approval,
    }
    after = {
        "recipeId": current_review.recipe_id,
        "revision": current_review.revision,
        "maturity": current_review.maturity.value,
        "identity": current_review.identity.value,
        "sourceSha256": configuration.recipe.source.source_sha256,
        "selected": current_review.selected,
        "omitted": current_review.omitted,
        "readyForApproval": current_review.ready_for_approval,
    }
    return DriftDomain("recipe-intent", tuple(_field_changes(before, after)))


def _compare_target_environment(
    configuration: SkyrimEnvironmentConfiguration,
    baseline: ObservedBaselineEvidence,
    current: SkyrimLiveObservation,
) -> DriftDomain:
    before_findings = tuple(
        f"{item.dimension}|{item.target_value}|{item.actual_value}|{item.state.value}"
        for item in baseline.target_environment.live_dimension_findings
    )
    after_findings = tuple(
        f"{item.dimension}|{item.target_value}|{item.actual_value}|{item.state.value}"
        for item in current.dimension_findings
    )
    before = {
        "environmentSourceSha256": (
            baseline.target_environment.environment_source_sha256
        ),
        "environmentId": baseline.target_environment.environment_id,
        "dimensions": tuple(
            f"{key}={value}"
            for key, value in baseline.target_environment.dimensions
        ),
        "liveDimensionFindings": before_findings,
    }
    after = {
        "environmentSourceSha256": (
            configuration.target_environment.source.source_sha256
        ),
        "environmentId": configuration.target_environment.environment_id,
        "dimensions": tuple(
            f"{key}={value}"
            for key, value in configuration.target_environment.dimensions
        ),
        "liveDimensionFindings": after_findings,
    }
    return DriftDomain("target-environment", tuple(_field_changes(before, after)))


def _compare_coverage(
    baseline: ObservedBaselineEvidence,
    current: SkyrimLiveObservation,
) -> DriftDomain:
    coverage = current.projection.coverage
    before = {
        "foundationAssembly": "NotVerified",
        "profileState": "Complete",
        "installedPayloadContent": "NotInspected",
        "artifactCoverage": "NotLinked",
        "assetConflicts": "NotInspected",
        "pluginRecordConflicts": "NotInspected",
        "runtimeValidation": "NotPerformed",
        "smokeTest": "NotPerformed",
    }
    after = {
        "foundationAssembly": "NotVerified",
        "profileState": _claim_case(coverage.profile_state),
        "installedPayloadContent": _claim_case(
            coverage.installed_payload_content
        ),
        "artifactCoverage": "NotLinked",
        "assetConflicts": _claim_case(coverage.asset_conflicts),
        "pluginRecordConflicts": _claim_case(
            coverage.plugin_record_conflicts
        ),
        "runtimeValidation": _claim_case(coverage.runtime_validation),
        "smokeTest": "NotPerformed",
    }
    return DriftDomain("coverage", tuple(_field_changes(before, after)))


def _named_entry_changes(
    field: str,
    before,
    after,
    identity,
    *,
    name_getter=lambda item: item.name,
) -> list[DriftValue]:
    before_names = tuple(name_getter(item) for item in before)
    after_names = tuple(name_getter(item) for item in after)
    changes = _sequence_changes(field, before_names, after_names)
    before_by_name = {name_getter(item): identity(item) for item in before}
    after_by_name = {name_getter(item): identity(item) for item in after}
    changed = tuple(
        f"{name}: checkpoint={before_by_name[name]} current={after_by_name[name]}"
        for name in sorted(set(before_by_name) & set(after_by_name))
        if before_by_name[name] != after_by_name[name]
    )
    if changed:
        changes.append(
            DriftValue(
                f"{field}.attributes",
                _bounded_items(changed),
                _bounded_items(changed),
            )
        )
    return changes


def _sequence_changes(
    field: str, before: tuple[str, ...], after: tuple[str, ...]
) -> list[DriftValue]:
    if before == after:
        return []
    changes: list[DriftValue] = []
    before_set = set(before)
    after_set = set(after)
    if before_set != after_set:
        changes.append(
            DriftValue(
                f"{field}.membership",
                _bounded_items(before),
                _bounded_items(after),
            )
        )
    if before_set == after_set and before != after:
        left, right = _divergence_windows(before, after)
        changes.append(DriftValue(f"{field}.order", left, right))
    return changes


def _field_changes(before: dict[str, DriftScalar], after: dict[str, DriftScalar]):
    changes: list[DriftValue] = []
    for field in sorted(set(before) | set(after)):
        left = before.get(field)
        right = after.get(field)
        if left == right:
            continue
        if isinstance(left, tuple) and isinstance(right, tuple):
            left, right = _divergence_windows(left, right)
        changes.append(DriftValue(field, left, right))
    return changes


def _divergence_windows(
    before: tuple[str, ...], after: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    first = next(
        (
            index
            for index in range(max(len(before), len(after)))
            if index >= len(before)
            or index >= len(after)
            or before[index] != after[index]
        ),
        0,
    )
    start = max(0, first - 3)

    def window(value: tuple[str, ...]) -> tuple[str, ...]:
        return (
            f"count={len(value)}",
            f"firstDifference={first}",
            *(
                f"[{start + index}]={item}"
                for index, item in enumerate(value[start : start + 7])
            ),
        )

    return window(before), window(after)


def _bounded_items(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) <= 7:
        return value
    return (f"count={len(value)}", *value[:7])


def _meta_identity(value) -> str:
    if value is None:
        return "none"
    return f"{value.relative_path}|{value.sha256}|{value.size}"


def _claim_case(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("-"))


def _value(value, attribute: str):
    return None if value is None else getattr(value, attribute)


def _blocked(baseline_id: str, message: str) -> SkyrimDriftReport:
    return SkyrimDriftReport(
        SkyrimStatusOutcome.BLOCKED,
        baseline_id,
        (),
        (message,),
    )


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
