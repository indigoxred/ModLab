"""Immutable observed-baseline capture and explicit pointer selection."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from modlab.adapters.mo2.comparison_serialization import (
    Mo2ComparisonFormatError,
    adapter_state_from_dict,
)
from modlab.adapters.mo2.projection import (
    Mo2Readiness,
    adapter_state_sha256,
    adapter_state_to_dict,
)
from modlab.checkpoints.model import (
    CheckpointDraft,
    CheckpointHealth,
    CheckpointRecord,
)
from modlab.checkpoints.serialization import (
    CheckpointFormatError,
    checkpoint_record_from_draft,
)
from modlab.checkpoints.store import (
    CheckpointNotFoundError,
    CheckpointStore,
    CheckpointStoreError,
)
from modlab.recipes.loading import (
    RecipeFormatError,
    load_environment_source,
    load_recipe_source,
)
from modlab.recipes.model import RecipeReview
from modlab.recipes.reviewing import review_recipe

from .configuration import SkyrimEnvironmentConfiguration
from .evidence import (
    BaselineEvidenceFormatError,
    BaselineRecipeEvidence,
    BaselineTargetEnvironmentEvidence,
    ObservedBaselineEvidence,
    baseline_evidence_from_dict,
    baseline_evidence_to_dict,
)
from .observation import SkyrimLiveObservation
from .store import (
    ConfigurationSnapshot,
    ConfigurationWrite,
    SkyrimEnvironmentConflictError,
    SkyrimEnvironmentStore,
    SkyrimEnvironmentStoreError,
)


class SkyrimBaselineCaptureError(RuntimeError):
    """Current intent or evidence cannot safely form an observed baseline."""


class SkyrimBaselineSelectionError(RuntimeError):
    """A checkpoint cannot safely become this environment's baseline."""


@dataclass(frozen=True)
class BaselineCaptureResult:
    checkpoint: CheckpointRecord
    selected: bool
    configuration: SkyrimEnvironmentConfiguration
    actions_performed: tuple[Path, ...]
    warning: str | None


def create_observed_baseline(
    snapshot: ConfigurationSnapshot,
    observation: SkyrimLiveObservation,
    recipe_review: RecipeReview,
    store: SkyrimEnvironmentStore,
    checkpoint_store: CheckpointStore,
    *,
    clock,
) -> BaselineCaptureResult:
    configuration = _validate_capture_inputs(
        snapshot, observation, recipe_review, store, checkpoint_store
    )
    projection = observation.projection
    if projection is None or projection.adapter_state is None:
        raise SkyrimBaselineCaptureError(
            "Ready observation must contain canonical MO2 adapter state"
        )
    state = projection.adapter_state
    parent: CheckpointRecord | None = None
    if configuration.baseline_checkpoint_id is not None:
        try:
            parent = _validated_checkpoint(
                configuration.baseline_checkpoint_id,
                configuration,
                checkpoint_store,
            )
        except SkyrimBaselineSelectionError as error:
            raise SkyrimBaselineCaptureError(
                f"Selected parent baseline is invalid: {error}"
            ) from error

    created_at = _utc_seconds(clock)
    evidence = ObservedBaselineEvidence(
        game_discovery=observation.discovery,
        manager_observation_context=projection.observation_context,
        manager_capabilities=projection.capabilities,
        manager_findings=projection.findings,
        adapter_state_sha256=adapter_state_sha256(state),
        recipe_review=BaselineRecipeEvidence(
            recipe_source_sha256=configuration.recipe.source.source_sha256,
            selected=configuration.recipe.selected,
            omitted=configuration.recipe.omitted,
            identity=configuration.recipe.identity,
            ready_for_approval=True,
        ),
        target_environment=BaselineTargetEnvironmentEvidence(
            environment_source_sha256=(
                configuration.target_environment.source.source_sha256
            ),
            environment_id=configuration.target_environment.environment_id,
            dimensions=configuration.target_environment.dimensions,
            live_dimension_findings=observation.dimension_findings,
        ),
    )
    try:
        evidence_json = _canonical_json(
            baseline_evidence_to_dict(evidence, adapter_state=state)
        )
    except BaselineEvidenceFormatError as error:
        raise SkyrimBaselineCaptureError(
            f"Live observation cannot form baseline evidence: {error}"
        ) from error
    draft = CheckpointDraft(
        schema_version=1,
        game="skyrim-se-ae",
        environment_id=configuration.environment_id,
        adapter_id="portable-mo2-skyrim",
        created_at=created_at,
        parent_checkpoint_id=(
            None if parent is None else parent.checkpoint_id
        ),
        lineage_id=configuration.lineage_id,
        recipe_id=configuration.recipe.recipe_id,
        recipe_revision=configuration.recipe.revision,
        recipe_maturity=configuration.recipe.maturity,
        recipe_identity=configuration.recipe.identity,
        artifact_ids=(),
        adapter_state_json=_canonical_json(adapter_state_to_dict(state)),
        evidence_json=evidence_json,
    )

    actions: list[Path] = []
    checkpoint: CheckpointRecord
    if parent is not None and _same_draft(parent, replace(
        draft, parent_checkpoint_id=parent.parent_checkpoint_id
    )):
        checkpoint = parent
    else:
        expected = checkpoint_record_from_draft(draft)
        checkpoint_path = checkpoint_store.path_for(expected.checkpoint_id)
        existed = checkpoint_path.exists()
        try:
            checkpoint = checkpoint_store.create(draft)
        except (CheckpointFormatError, CheckpointStoreError) as error:
            raise SkyrimBaselineCaptureError(
                f"Could not store observed baseline checkpoint: {error}"
            ) from error
        if not existed and checkpoint_path.is_file():
            actions.append(checkpoint_path)

    desired = replace(
        configuration, baseline_checkpoint_id=checkpoint.checkpoint_id
    )
    try:
        write = store.compare_and_swap(snapshot.sha256, desired)
    except (SkyrimEnvironmentConflictError, SkyrimEnvironmentStoreError) as error:
        try:
            current_configuration = store.load().configuration
        except SkyrimEnvironmentStoreError:
            current_configuration = configuration
        return BaselineCaptureResult(
            checkpoint=checkpoint,
            selected=False,
            configuration=current_configuration,
            actions_performed=tuple(actions),
            warning=(
                f"Checkpoint {checkpoint.checkpoint_id} is available but was not "
                f"selected because the configuration pointer update failed: {error}"
            ),
        )
    actions.extend(write.paths_written)
    return BaselineCaptureResult(
        checkpoint=checkpoint,
        selected=True,
        configuration=write.configuration,
        actions_performed=tuple(actions),
        warning=None,
    )


def select_observed_baseline(
    snapshot: ConfigurationSnapshot,
    checkpoint_id: str,
    store: SkyrimEnvironmentStore,
    checkpoint_store: CheckpointStore,
) -> ConfigurationWrite:
    try:
        current = store.load()
    except SkyrimEnvironmentStoreError as error:
        raise SkyrimBaselineSelectionError(
            f"Cannot load current configuration: {error}"
        ) from error
    if current.sha256 != snapshot.sha256 or current.data != snapshot.data:
        raise SkyrimBaselineSelectionError(
            "Skyrim environment configuration changed after observation"
        )
    _validated_checkpoint(
        checkpoint_id, snapshot.configuration, checkpoint_store
    )
    desired = replace(
        snapshot.configuration, baseline_checkpoint_id=checkpoint_id
    )
    try:
        return store.compare_and_swap(snapshot.sha256, desired)
    except SkyrimEnvironmentStoreError as error:
        raise SkyrimBaselineSelectionError(
            f"Could not select observed baseline: {error}"
        ) from error


def _validate_capture_inputs(
    snapshot: ConfigurationSnapshot,
    observation: SkyrimLiveObservation,
    supplied_review: RecipeReview,
    store: SkyrimEnvironmentStore,
    checkpoint_store: CheckpointStore,
) -> SkyrimEnvironmentConfiguration:
    try:
        current = store.load()
    except SkyrimEnvironmentStoreError as error:
        raise SkyrimBaselineCaptureError(
            f"Cannot load current configuration: {error}"
        ) from error
    if current.sha256 != snapshot.sha256 or current.data != snapshot.data:
        raise SkyrimBaselineCaptureError(
            "Skyrim environment configuration changed after observation"
        )
    configuration = current.configuration
    if checkpoint_store.game_key != configuration.game_key:
        raise SkyrimBaselineCaptureError(
            "Checkpoint store game does not match configured game"
        )
    if (
        not observation.ready
        or observation.projection is None
        or observation.projection.readiness is not Mo2Readiness.READY
        or observation.projection.adapter_state is None
        or observation.projection.adapter_state_sha256 is None
        or observation.projection.observation_context.executable is None
        or not observation.projection.observation_context.read_set_stable
        or observation.projection.observation_context.read_set_sha256 is None
        or observation.discovery.executable is None
    ):
        raise SkyrimBaselineCaptureError(
            "Live Skyrim/MO2 observation is not complete and Ready"
        )
    if adapter_state_sha256(observation.projection.adapter_state) != (
        observation.projection.adapter_state_sha256
    ):
        raise SkyrimBaselineCaptureError(
            "Live MO2 adapter state hash is inconsistent"
        )
    if (
        PureWindowsPath(observation.discovery.steam_root)
        != PureWindowsPath(configuration.steam_root)
        or observation.discovery.game_root is None
        or PureWindowsPath(observation.discovery.game_root)
        != PureWindowsPath(configuration.game_root)
        or PureWindowsPath(
            observation.projection.observation_context.game_root
        )
        != PureWindowsPath(configuration.game_root)
        or PureWindowsPath(
            observation.projection.observation_context.mo2_root
        )
        != PureWindowsPath(
            store.layout.root / configuration.manager.root
        )
    ):
        raise SkyrimBaselineCaptureError(
            "Live observation paths do not match registered configuration"
        )

    recipe_path = store.layout.root / configuration.recipe.source.stored_path
    environment_path = (
        store.layout.root
        / configuration.target_environment.source.stored_path
    )
    try:
        recipe_source = load_recipe_source(recipe_path)
        environment_source = load_environment_source(environment_path)
    except RecipeFormatError as error:
        raise SkyrimBaselineCaptureError(
            f"Retained recipe or target environment is invalid: {error}"
        ) from error
    if (
        recipe_source.sha256 != configuration.recipe.source.source_sha256
        or recipe_source.recipe.recipe_id != configuration.recipe.recipe_id
        or recipe_source.recipe.revision != configuration.recipe.revision
        or recipe_source.recipe.maturity != configuration.recipe.maturity
    ):
        raise SkyrimBaselineCaptureError(
            "Retained recipe identity differs from configuration"
        )
    target_dimensions = tuple(
        sorted(environment_source.environment.dimensions.items())
    )
    if (
        environment_source.sha256
        != configuration.target_environment.source.source_sha256
        or environment_source.environment.environment_id
        != configuration.target_environment.environment_id
        or target_dimensions != configuration.target_environment.dimensions
    ):
        raise SkyrimBaselineCaptureError(
            "Retained target environment differs from configuration"
        )
    expected_review = review_recipe(
        recipe_source.recipe,
        environment_source.environment,
        select=configuration.recipe.selected,
        omit=configuration.recipe.omitted,
    )
    if (
        supplied_review != expected_review
        or not supplied_review.ready_for_approval
        or supplied_review.identity != configuration.recipe.identity
        or supplied_review.selected != configuration.recipe.selected
        or supplied_review.omitted != configuration.recipe.omitted
    ):
        raise SkyrimBaselineCaptureError(
            "Recipe review is not ready or differs from retained intent"
        )
    if tuple(
        (item.dimension, item.target_value)
        for item in observation.dimension_findings
    ) != target_dimensions:
        raise SkyrimBaselineCaptureError(
            "Live dimension findings do not match target environment"
        )
    return configuration


def _validated_checkpoint(
    checkpoint_id: str,
    configuration: SkyrimEnvironmentConfiguration,
    checkpoint_store: CheckpointStore,
) -> CheckpointRecord:
    if checkpoint_store.game_key != configuration.game_key:
        raise SkyrimBaselineSelectionError(
            "checkpoint store is for a different game"
        )
    try:
        finding = checkpoint_store.verify(checkpoint_id)
        if finding.health is not CheckpointHealth.AVAILABLE:
            raise SkyrimBaselineSelectionError(
                f"checkpoint is {finding.health.value}: {finding.message}"
            )
        record = checkpoint_store.get(checkpoint_id)
        state = adapter_state_from_dict(json.loads(record.adapter_state_json))
        evidence = baseline_evidence_from_dict(
            json.loads(record.evidence_json), adapter_state=state
        )
    except SkyrimBaselineSelectionError:
        raise
    except (
        CheckpointNotFoundError,
        CheckpointFormatError,
        Mo2ComparisonFormatError,
        BaselineEvidenceFormatError,
        json.JSONDecodeError,
    ) as error:
        raise SkyrimBaselineSelectionError(
            f"checkpoint cannot be validated: {error}"
        ) from error

    expected_record = {
        "game": configuration.game_key,
        "environment_id": configuration.environment_id,
        "adapter_id": configuration.manager.adapter_id,
        "lineage_id": configuration.lineage_id,
        "recipe_id": configuration.recipe.recipe_id,
        "recipe_revision": configuration.recipe.revision,
        "recipe_maturity": configuration.recipe.maturity,
        "recipe_identity": configuration.recipe.identity,
    }
    mismatched = [
        field
        for field, expected in expected_record.items()
        if getattr(record, field) != expected
    ]
    if mismatched:
        raise SkyrimBaselineSelectionError(
            "checkpoint cross-link differs: " + ", ".join(mismatched)
        )
    if record.artifact_ids:
        raise SkyrimBaselineSelectionError(
            "observed baseline must not contain artifact IDs"
        )
    if (
        evidence.recipe_review.recipe_source_sha256
        != configuration.recipe.source.source_sha256
        or evidence.recipe_review.selected != configuration.recipe.selected
        or evidence.recipe_review.omitted != configuration.recipe.omitted
        or evidence.recipe_review.identity != configuration.recipe.identity
        or not evidence.recipe_review.ready_for_approval
        or evidence.target_environment.environment_source_sha256
        != configuration.target_environment.source.source_sha256
        or evidence.target_environment.environment_id
        != configuration.target_environment.environment_id
        or evidence.target_environment.dimensions
        != configuration.target_environment.dimensions
    ):
        raise SkyrimBaselineSelectionError(
            "checkpoint recipe or target-environment evidence differs from configuration"
        )
    return record


def _same_draft(record: CheckpointRecord, draft: CheckpointDraft) -> bool:
    return all(
        getattr(record, field.name) == getattr(draft, field.name)
        for field in fields(CheckpointDraft)
    )


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_seconds(clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SkyrimBaselineCaptureError(
            "capture clock must return a timezone-aware datetime"
        )
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
