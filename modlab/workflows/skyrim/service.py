"""User-facing orchestration for Skyrim configuration, baseline, and status."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from modlab.adapters.mo2.comparison_serialization import (
    Mo2ComparisonFormatError,
    adapter_state_from_dict,
)
from modlab.adapters.skyrim.windows_version import read_windows_file_version
from modlab.checkpoints.model import CheckpointHealth
from modlab.checkpoints.serialization import CheckpointFormatError
from modlab.checkpoints.store import (
    CheckpointNotFoundError,
    CheckpointStore,
)
from modlab.recipes.loading import (
    LoadedEnvironmentSource,
    LoadedRecipeSource,
    RecipeFormatError,
    load_environment_source,
    load_recipe_source,
)
from modlab.recipes.model import RecipeReview
from modlab.recipes.reviewing import review_recipe

from .capture import (
    BaselineCaptureResult,
    SkyrimBaselineCaptureError,
    SkyrimBaselineSelectionError,
    create_observed_baseline,
    select_observed_baseline,
)
from .configuration import (
    ConfigurationChange,
    ManagerRegistration,
    RecipeIntent,
    SkyrimEnvironmentConfiguration,
    StoredSourceReference,
    TargetEnvironmentIntent,
    configuration_changes,
)
from .drift import (
    SkyrimDriftReport,
    SkyrimStatusOutcome,
    compare_observed_baseline,
)
from .evidence import (
    BaselineEvidenceFormatError,
    baseline_evidence_from_dict,
)
from .observation import SkyrimLiveObservation, observe_skyrim_environment
from .store import (
    ConfigurationSnapshot,
    SkyrimEnvironmentConflictError,
    SkyrimEnvironmentNotConfiguredError,
    SkyrimEnvironmentStore,
    SkyrimEnvironmentStoreError,
)


class SkyrimWorkflowError(RuntimeError):
    """A Skyrim workflow cannot proceed safely with the supplied evidence."""


class SkyrimConfigurationConflictError(SkyrimWorkflowError):
    """Existing intent differs and requires explicit replacement."""

    def __init__(
        self, message: str, changes: tuple[ConfigurationChange, ...]
    ):
        self.changes = changes
        super().__init__(message)


@dataclass(frozen=True)
class ConfigureResult:
    configuration: SkyrimEnvironmentConfiguration
    observation: SkyrimLiveObservation
    recipe_review: RecipeReview
    changed: bool
    actions_performed: tuple[str, ...]


@dataclass(frozen=True)
class BaselineUseResult:
    checkpoint_id: str
    configuration: SkyrimEnvironmentConfiguration
    changed: bool
    actions_performed: tuple[str, ...]


def configure_skyrim_environment(
    *,
    steam_root: Path,
    recipe_path: Path,
    target_environment_path: Path,
    workspace_root: Path,
    select: tuple[str, ...] = (),
    omit: tuple[str, ...] = (),
    replace_existing: bool = False,
    skyrim_version_reader: Callable[[Path], str | None] | None = None,
    mo2_version_reader: Callable[[Path], str | None] | None = None,
) -> ConfigureResult:
    skyrim_reader = skyrim_version_reader or read_windows_file_version
    mo2_reader = mo2_version_reader or read_windows_file_version
    try:
        recipe_source = load_recipe_source(recipe_path)
        target_source = load_environment_source(target_environment_path)
        observation = observe_skyrim_environment(
            steam_root,
            workspace_root,
            target_source.environment,
            skyrim_version_reader=skyrim_reader,
            mo2_version_reader=mo2_reader,
        )
        review = review_recipe(
            recipe_source.recipe,
            target_source.environment,
            select,
            omit,
        )
    except (RecipeFormatError, ValueError, OSError) as error:
        raise SkyrimWorkflowError(
            f"Skyrim configuration input is invalid: {error}"
        ) from error
    if not observation.ready:
        raise SkyrimWorkflowError(
            "Skyrim and portable MO2 are not complete, coherent, and Ready"
        )
    if not review.ready_for_approval:
        raise SkyrimWorkflowError(
            "Foundation Recipe selection is incomplete or incompatible"
        )
    if observation.discovery.game_root is None:
        raise SkyrimWorkflowError("Skyrim game root was not observed")

    proposed = _build_configuration(
        observation=observation,
        recipe_source=recipe_source,
        target_source=target_source,
        review=review,
    )
    store = SkyrimEnvironmentStore(workspace_root)
    proposed = _preflight_existing(
        store, proposed, replace_existing=replace_existing
    )
    _require_sources_unchanged(recipe_source, target_source)
    try:
        recipe_retention = store.retain_recipe(recipe_source)
        environment_retention = store.retain_target_environment(target_source)
        configuration = replace(
            proposed,
            recipe=replace(
                proposed.recipe, source=recipe_retention.reference
            ),
            target_environment=replace(
                proposed.target_environment,
                source=environment_retention.reference,
            ),
        )
        write = store.write(configuration, replace=replace_existing)
    except SkyrimEnvironmentConflictError as error:
        raise SkyrimConfigurationConflictError(str(error), error.changes) from error
    except SkyrimEnvironmentStoreError as error:
        raise SkyrimWorkflowError(
            f"Could not retain Skyrim configuration safely: {error}"
        ) from error

    action_paths: list[Path] = []
    if recipe_retention.changed:
        action_paths.append(recipe_retention.path)
    if environment_retention.changed:
        action_paths.append(environment_retention.path)
    action_paths.extend(write.paths_written)
    return ConfigureResult(
        configuration=write.configuration,
        observation=observation,
        recipe_review=review,
        changed=write.changed,
        actions_performed=tuple(str(path) for path in action_paths),
    )


def create_skyrim_baseline(
    workspace_root: Path,
    *,
    clock,
    skyrim_version_reader: Callable[[Path], str | None] | None = None,
    mo2_version_reader: Callable[[Path], str | None] | None = None,
) -> BaselineCaptureResult:
    skyrim_reader = skyrim_version_reader or read_windows_file_version
    mo2_reader = mo2_version_reader or read_windows_file_version
    store = SkyrimEnvironmentStore(workspace_root)
    try:
        snapshot = store.load()
        _, target_source, review = _load_registered_intent(snapshot, store)
        observation = observe_skyrim_environment(
            Path(snapshot.configuration.steam_root),
            workspace_root,
            target_source.environment,
            skyrim_version_reader=skyrim_reader,
            mo2_version_reader=mo2_reader,
        )
        return create_observed_baseline(
            snapshot,
            observation,
            review,
            store,
            CheckpointStore(workspace_root, "skyrim-se-ae"),
            clock=clock,
        )
    except (
        SkyrimEnvironmentNotConfiguredError,
        SkyrimEnvironmentStoreError,
        SkyrimBaselineCaptureError,
        RecipeFormatError,
        ValueError,
    ) as error:
        raise SkyrimWorkflowError(
            f"Could not create Skyrim observed baseline: {error}"
        ) from error


def use_skyrim_baseline(
    workspace_root: Path, checkpoint_id: str
) -> BaselineUseResult:
    store = SkyrimEnvironmentStore(workspace_root)
    try:
        snapshot = store.load()
        _load_registered_intent(snapshot, store)
        write = select_observed_baseline(
            snapshot,
            checkpoint_id,
            store,
            CheckpointStore(workspace_root, "skyrim-se-ae"),
        )
    except (
        SkyrimEnvironmentNotConfiguredError,
        SkyrimEnvironmentStoreError,
        SkyrimBaselineSelectionError,
        RecipeFormatError,
        ValueError,
    ) as error:
        raise SkyrimWorkflowError(
            f"Could not select Skyrim observed baseline: {error}"
        ) from error
    return BaselineUseResult(
        checkpoint_id=checkpoint_id,
        configuration=write.configuration,
        changed=write.changed,
        actions_performed=tuple(str(path) for path in write.paths_written),
    )


def get_skyrim_status(
    workspace_root: Path,
    *,
    skyrim_version_reader: Callable[[Path], str | None] | None = None,
    mo2_version_reader: Callable[[Path], str | None] | None = None,
) -> SkyrimDriftReport:
    skyrim_reader = skyrim_version_reader or read_windows_file_version
    mo2_reader = mo2_version_reader or read_windows_file_version
    store = SkyrimEnvironmentStore(workspace_root)
    try:
        snapshot = store.load()
    except (SkyrimEnvironmentNotConfiguredError, SkyrimEnvironmentStoreError) as error:
        return _blocked_status(None, f"Cannot load Skyrim configuration: {error}")
    configuration = snapshot.configuration
    try:
        _, target_source, review = _load_registered_intent(snapshot, store)
        observation = observe_skyrim_environment(
            Path(configuration.steam_root),
            workspace_root,
            target_source.environment,
            skyrim_version_reader=skyrim_reader,
            mo2_version_reader=mo2_reader,
        )
    except (RecipeFormatError, SkyrimWorkflowError, ValueError, OSError) as error:
        return _blocked_status(
            configuration.baseline_checkpoint_id,
            f"Cannot verify retained Skyrim intent: {error}",
        )

    checkpoint_id = configuration.baseline_checkpoint_id
    if checkpoint_id is None:
        return compare_observed_baseline(
            configuration, None, None, observation, review
        )
    checkpoint_store = CheckpointStore(workspace_root, "skyrim-se-ae")
    try:
        finding = checkpoint_store.verify(checkpoint_id)
        if finding.health is not CheckpointHealth.AVAILABLE:
            return _blocked_status(checkpoint_id, finding.message)
        checkpoint = checkpoint_store.get(checkpoint_id)
        state = adapter_state_from_dict(
            json.loads(checkpoint.adapter_state_json)
        )
        evidence = baseline_evidence_from_dict(
            json.loads(checkpoint.evidence_json), adapter_state=state
        )
    except (
        CheckpointNotFoundError,
        CheckpointFormatError,
        Mo2ComparisonFormatError,
        BaselineEvidenceFormatError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        return _blocked_status(
            checkpoint_id, f"Cannot verify selected checkpoint: {error}"
        )
    return compare_observed_baseline(
        configuration, checkpoint, evidence, observation, review
    )


def _build_configuration(
    *,
    observation: SkyrimLiveObservation,
    recipe_source: LoadedRecipeSource,
    target_source: LoadedEnvironmentSource,
    review: RecipeReview,
) -> SkyrimEnvironmentConfiguration:
    recipe_reference = StoredSourceReference(
        recipe_source.sha256,
        (
            "games/skyrim-se-ae/recipes/"
            f"{recipe_source.sha256}/recipe.json"
        ),
    )
    target_reference = StoredSourceReference(
        target_source.sha256,
        (
            "games/skyrim-se-ae/target-environments/"
            f"{target_source.sha256}/environment.json"
        ),
    )
    return SkyrimEnvironmentConfiguration(
        schema_version=1,
        game_key="skyrim-se-ae",
        environment_id=target_source.environment.environment_id,
        lineage_id="skyrim-main",
        steam_root=observation.discovery.steam_root,
        game_root=observation.discovery.game_root,
        manager=ManagerRegistration(
            "portable-mo2-skyrim", "tools/mo2/skyrim-se-ae/app"
        ),
        recipe=RecipeIntent(
            recipe_source.recipe.recipe_id,
            recipe_source.recipe.revision,
            recipe_source.recipe.maturity,
            review.identity,
            recipe_reference,
            review.selected,
            review.omitted,
        ),
        target_environment=TargetEnvironmentIntent(
            target_source.environment.environment_id,
            target_reference,
            tuple(sorted(target_source.environment.dimensions.items())),
        ),
        baseline_checkpoint_id=None,
    )


def _preflight_existing(
    store: SkyrimEnvironmentStore,
    proposed: SkyrimEnvironmentConfiguration,
    *,
    replace_existing: bool,
) -> SkyrimEnvironmentConfiguration:
    try:
        existing = store.load()
    except SkyrimEnvironmentNotConfiguredError:
        return proposed
    except SkyrimEnvironmentStoreError as error:
        raise SkyrimWorkflowError(
            f"Existing Skyrim configuration is invalid and was not overwritten: {error}"
        ) from error

    without_pointer = replace(
        existing.configuration, baseline_checkpoint_id=None
    )
    if without_pointer == proposed:
        proposed = replace(
            proposed,
            baseline_checkpoint_id=existing.configuration.baseline_checkpoint_id,
        )
    if existing.configuration == proposed:
        return proposed
    changes = configuration_changes(existing.configuration, proposed)
    if not replace_existing:
        raise SkyrimConfigurationConflictError(
            "Skyrim configuration differs; rerun with explicit replacement after reviewing the changes",
            changes,
        )
    return proposed


def _require_sources_unchanged(
    recipe_source: LoadedRecipeSource,
    target_source: LoadedEnvironmentSource,
) -> None:
    try:
        current_recipe = load_recipe_source(recipe_source.path)
        current_target = load_environment_source(target_source.path)
    except RecipeFormatError as error:
        raise SkyrimWorkflowError(
            f"A selected source changed before retention: {error}"
        ) from error
    if (
        current_recipe.data != recipe_source.data
        or current_recipe.sha256 != recipe_source.sha256
        or current_target.data != target_source.data
        or current_target.sha256 != target_source.sha256
    ):
        raise SkyrimWorkflowError(
            "A selected recipe or target-environment source changed before retention"
        )


def _load_registered_intent(
    snapshot: ConfigurationSnapshot,
    store: SkyrimEnvironmentStore,
) -> tuple[LoadedRecipeSource, LoadedEnvironmentSource, RecipeReview]:
    configuration = snapshot.configuration
    recipe_path = store.layout.root / configuration.recipe.source.stored_path
    target_path = (
        store.layout.root
        / configuration.target_environment.source.stored_path
    )
    try:
        recipe_source = load_recipe_source(recipe_path)
        target_source = load_environment_source(target_path)
        review = review_recipe(
            recipe_source.recipe,
            target_source.environment,
            select=configuration.recipe.selected,
            omit=configuration.recipe.omitted,
        )
    except (RecipeFormatError, ValueError) as error:
        raise SkyrimWorkflowError(
            f"Retained recipe or target environment is invalid: {error}"
        ) from error
    if (
        recipe_source.sha256 != configuration.recipe.source.source_sha256
        or recipe_source.recipe.recipe_id != configuration.recipe.recipe_id
        or recipe_source.recipe.revision != configuration.recipe.revision
        or recipe_source.recipe.maturity != configuration.recipe.maturity
        or target_source.sha256
        != configuration.target_environment.source.source_sha256
        or target_source.environment.environment_id
        != configuration.target_environment.environment_id
        or tuple(sorted(target_source.environment.dimensions.items()))
        != configuration.target_environment.dimensions
        or review.identity != configuration.recipe.identity
        or review.selected != configuration.recipe.selected
        or review.omitted != configuration.recipe.omitted
        or not review.ready_for_approval
    ):
        raise SkyrimWorkflowError(
            "Retained recipe review or target identity differs from configuration"
        )
    return recipe_source, target_source, review


def _blocked_status(
    checkpoint_id: str | None, message: str
) -> SkyrimDriftReport:
    return SkyrimDriftReport(
        SkyrimStatusOutcome.BLOCKED,
        checkpoint_id,
        (),
        (message,),
    )
