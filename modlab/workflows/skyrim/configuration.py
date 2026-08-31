"""Strict canonical configuration for one managed Skyrim environment."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from modlab.recipes.model import RecipeIdentity, RecipeMaturity


class SkyrimConfigurationFormatError(ValueError):
    """The Skyrim environment configuration is malformed or unsafe."""


@dataclass(frozen=True)
class StoredSourceReference:
    source_sha256: str
    stored_path: str


@dataclass(frozen=True)
class ManagerRegistration:
    adapter_id: str
    root: str


@dataclass(frozen=True)
class RecipeIntent:
    recipe_id: str
    revision: str
    maturity: RecipeMaturity
    identity: RecipeIdentity
    source: StoredSourceReference
    selected: tuple[str, ...]
    omitted: tuple[str, ...]


@dataclass(frozen=True)
class TargetEnvironmentIntent:
    environment_id: str
    source: StoredSourceReference
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SkyrimEnvironmentConfiguration:
    schema_version: int
    game_key: str
    environment_id: str
    lineage_id: str
    steam_root: str
    game_root: str
    manager: ManagerRegistration
    recipe: RecipeIntent
    target_environment: TargetEnvironmentIntent
    baseline_checkpoint_id: str | None


@dataclass(frozen=True)
class ConfigurationChange:
    field: str
    before: str | bool | None | tuple[str, ...]
    after: str | bool | None | tuple[str, ...]


_CONFIGURATION_FIELDS = {
    "schemaVersion",
    "gameKey",
    "environmentId",
    "lineageId",
    "steamRoot",
    "gameRoot",
    "manager",
    "recipe",
    "targetEnvironment",
    "baselineCheckpointId",
}
_MANAGER_FIELDS = {"adapterId", "root"}
_RECIPE_FIELDS = {
    "recipeId",
    "revision",
    "maturity",
    "identity",
    "sourceSha256",
    "storedPath",
    "selected",
    "omitted",
}
_TARGET_ENVIRONMENT_FIELDS = {"sourceSha256", "storedPath", "dimensions"}
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIMENSION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_ID = re.compile(r"^checkpoint-sha256:[0-9a-f]{64}$")
_GAME_KEY = "skyrim-se-ae"
_ADAPTER_ID = "portable-mo2-skyrim"
_MANAGER_ROOT = "tools/mo2/skyrim-se-ae/app"


def _exact_mapping(
    value: object, expected_fields: set[str], label: str
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SkyrimConfigurationFormatError(f"{label} must be an object")
    actual_fields = set(value)
    if actual_fields != expected_fields:
        raise SkyrimConfigurationFormatError(
            f"{label} fields differ: "
            f"missing={sorted(expected_fields - actual_fields)}, "
            f"extra={sorted(actual_fields - expected_fields)}"
        )
    return value


def _without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SkyrimConfigurationFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_save_reference(value: str, field: str) -> None:
    normalized = value.replace("\\", "/")
    parts = [part.lower() for part in normalized.split("/") if part]
    if "saves" in parts or normalized.lower().endswith((".ess", ".skse")):
        raise SkyrimConfigurationFormatError(
            f"{field} must not reference saves or co-saves"
        )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SkyrimConfigurationFormatError(
            f"{field} must be non-empty text without surrounding whitespace"
        )
    _reject_save_reference(value, field)
    return value


def _safe_id(value: object, field: str) -> str:
    text = _text(value, field)
    if _SAFE_ID.fullmatch(text) is None:
        raise SkyrimConfigurationFormatError(
            f"{field} must use lowercase letters, digits, dots, underscores, or hyphens"
        )
    return text


def _sha256(value: object, field: str) -> str:
    text = _text(value, field)
    if _SHA256.fullmatch(text) is None:
        raise SkyrimConfigurationFormatError(
            f"{field} must be exactly 64 lowercase hexadecimal characters"
        )
    return text


def _enum_value(enum_type, value: object, field: str):
    text = _text(value, field)
    try:
        return enum_type(text)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise SkyrimConfigurationFormatError(
            f"{field} must be one of: {allowed}"
        ) from error


def _external_windows_path(value: object, field: str) -> str:
    text = _text(value, field)
    path = PureWindowsPath(text)
    raw_parts = text.replace("\\", "/").split("/")
    if (
        not path.is_absolute()
        or not path.drive
        or any(part in {"", ".", ".."} for part in raw_parts[1:])
    ):
        raise SkyrimConfigurationFormatError(
            f"{field} must be a direct absolute Windows path"
        )
    return text


def _workspace_relative_path(value: object, field: str) -> str:
    text = _text(value, field)
    raw_parts = text.split("/")
    path = PurePosixPath(text)
    if (
        "\\" in text
        or path.is_absolute()
        or ":" in raw_parts[0]
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise SkyrimConfigurationFormatError(
            f"{field} must be a safe workspace-relative POSIX path"
        )
    return text


def _stored_source(
    data: Mapping[str, object],
    *,
    label: str,
    directory: str,
    filename: str,
) -> StoredSourceReference:
    source_sha256 = _sha256(data["sourceSha256"], f"{label}.sourceSha256")
    stored_path = _workspace_relative_path(
        data["storedPath"], f"{label}.storedPath"
    )
    expected_path = (
        f"games/skyrim-se-ae/{directory}/{source_sha256}/{filename}"
    )
    if stored_path != expected_path:
        raise SkyrimConfigurationFormatError(
            f"{label}.storedPath must match its content address: {expected_path}"
        )
    return StoredSourceReference(
        source_sha256=source_sha256,
        stored_path=stored_path,
    )


def _ordered_ids(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SkyrimConfigurationFormatError(f"{field} must be an array")
    result = tuple(
        _safe_id(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    )
    if result != tuple(sorted(result)) or len(set(result)) != len(result):
        raise SkyrimConfigurationFormatError(
            f"{field} must be sorted and contain no duplicates"
        )
    return result


def _dimensions(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise SkyrimConfigurationFormatError(
            "targetEnvironment.dimensions must be an object"
        )
    keys = list(value)
    if any(not isinstance(key, str) for key in keys) or keys != sorted(keys):
        raise SkyrimConfigurationFormatError(
            "targetEnvironment.dimensions must have sorted text keys"
        )
    result: list[tuple[str, str]] = []
    for key in keys:
        if _DIMENSION_NAME.fullmatch(key) is None:
            raise SkyrimConfigurationFormatError(
                f"targetEnvironment.dimensions key is unsafe: {key!r}"
            )
        result.append(
            (
                key,
                _text(
                    value[key], f"targetEnvironment.dimensions.{key}"
                ),
            )
        )
    return tuple(result)


def configuration_from_dict(value: object) -> SkyrimEnvironmentConfiguration:
    data = _exact_mapping(value, _CONFIGURATION_FIELDS, "configuration")
    schema_version = data["schemaVersion"]
    if type(schema_version) is not int or schema_version != 1:
        raise SkyrimConfigurationFormatError("schemaVersion must be integer 1")

    game_key = _safe_id(data["gameKey"], "gameKey")
    if game_key != _GAME_KEY:
        raise SkyrimConfigurationFormatError(f"gameKey must be {_GAME_KEY}")
    environment_id = _safe_id(data["environmentId"], "environmentId")
    lineage_id = _safe_id(data["lineageId"], "lineageId")

    manager_data = _exact_mapping(data["manager"], _MANAGER_FIELDS, "manager")
    adapter_id = _safe_id(manager_data["adapterId"], "manager.adapterId")
    if adapter_id != _ADAPTER_ID:
        raise SkyrimConfigurationFormatError(
            f"manager.adapterId must be {_ADAPTER_ID}"
        )
    manager_root = _workspace_relative_path(manager_data["root"], "manager.root")
    if manager_root != _MANAGER_ROOT:
        raise SkyrimConfigurationFormatError(
            f"manager.root must be {_MANAGER_ROOT}"
        )

    recipe_data = _exact_mapping(data["recipe"], _RECIPE_FIELDS, "recipe")
    recipe_identity = _enum_value(
        RecipeIdentity, recipe_data["identity"], "recipe.identity"
    )
    if recipe_identity is RecipeIdentity.INCOMPLETE:
        raise SkyrimConfigurationFormatError(
            "recipe.identity must be Original or Custom, not Incomplete"
        )
    selected = _ordered_ids(recipe_data["selected"], "recipe.selected")
    omitted = _ordered_ids(recipe_data["omitted"], "recipe.omitted")
    overlap = sorted(set(selected) & set(omitted))
    if overlap:
        raise SkyrimConfigurationFormatError(
            "recipe.selected and recipe.omitted overlap: " + ", ".join(overlap)
        )

    target_data = _exact_mapping(
        data["targetEnvironment"],
        _TARGET_ENVIRONMENT_FIELDS,
        "targetEnvironment",
    )
    baseline_checkpoint_id = data["baselineCheckpointId"]
    if baseline_checkpoint_id is not None:
        baseline_checkpoint_id = _text(
            baseline_checkpoint_id, "baselineCheckpointId"
        )
        if _CHECKPOINT_ID.fullmatch(baseline_checkpoint_id) is None:
            raise SkyrimConfigurationFormatError(
                "baselineCheckpointId must be checkpoint-sha256:<64 lowercase hex> or null"
            )

    return SkyrimEnvironmentConfiguration(
        schema_version=schema_version,
        game_key=game_key,
        environment_id=environment_id,
        lineage_id=lineage_id,
        steam_root=_external_windows_path(data["steamRoot"], "steamRoot"),
        game_root=_external_windows_path(data["gameRoot"], "gameRoot"),
        manager=ManagerRegistration(adapter_id=adapter_id, root=manager_root),
        recipe=RecipeIntent(
            recipe_id=_safe_id(recipe_data["recipeId"], "recipe.recipeId"),
            revision=_safe_id(recipe_data["revision"], "recipe.revision"),
            maturity=_enum_value(
                RecipeMaturity, recipe_data["maturity"], "recipe.maturity"
            ),
            identity=recipe_identity,
            source=_stored_source(
                recipe_data,
                label="recipe",
                directory="recipes",
                filename="recipe.json",
            ),
            selected=selected,
            omitted=omitted,
        ),
        target_environment=TargetEnvironmentIntent(
            environment_id=environment_id,
            source=_stored_source(
                target_data,
                label="targetEnvironment",
                directory="target-environments",
                filename="environment.json",
            ),
            dimensions=_dimensions(target_data["dimensions"]),
        ),
        baseline_checkpoint_id=baseline_checkpoint_id,
    )


def _configuration_to_raw(
    value: SkyrimEnvironmentConfiguration,
) -> dict[str, object]:
    return {
        "schemaVersion": value.schema_version,
        "gameKey": value.game_key,
        "environmentId": value.environment_id,
        "lineageId": value.lineage_id,
        "steamRoot": value.steam_root,
        "gameRoot": value.game_root,
        "manager": {
            "adapterId": value.manager.adapter_id,
            "root": value.manager.root,
        },
        "recipe": {
            "recipeId": value.recipe.recipe_id,
            "revision": value.recipe.revision,
            "maturity": value.recipe.maturity.value,
            "identity": value.recipe.identity.value,
            "sourceSha256": value.recipe.source.source_sha256,
            "storedPath": value.recipe.source.stored_path,
            "selected": list(value.recipe.selected),
            "omitted": list(value.recipe.omitted),
        },
        "targetEnvironment": {
            "sourceSha256": value.target_environment.source.source_sha256,
            "storedPath": value.target_environment.source.stored_path,
            "dimensions": dict(value.target_environment.dimensions),
        },
        "baselineCheckpointId": value.baseline_checkpoint_id,
    }


def configuration_to_dict(
    value: SkyrimEnvironmentConfiguration,
) -> dict[str, object]:
    if value.target_environment.environment_id != value.environment_id:
        raise SkyrimConfigurationFormatError(
            "targetEnvironment.environmentId must match environmentId"
        )
    normalized = configuration_from_dict(_configuration_to_raw(value))
    return _configuration_to_raw(normalized)


def configuration_from_bytes(data: bytes) -> SkyrimEnvironmentConfiguration:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkyrimConfigurationFormatError(
            f"configuration must be UTF-8: {error}"
        ) from error
    try:
        value = json.loads(text, object_pairs_hook=_without_duplicate_keys)
    except json.JSONDecodeError as error:
        raise SkyrimConfigurationFormatError(
            "configuration is not valid JSON: "
            f"line {error.lineno}, column {error.colno}"
        ) from error
    return configuration_from_dict(value)


def configuration_bytes(value: SkyrimEnvironmentConfiguration) -> bytes:
    return (
        json.dumps(
            configuration_to_dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _flatten_configuration(
    value: SkyrimEnvironmentConfiguration,
) -> dict[str, str | bool | None | tuple[str, ...]]:
    return {
        "schemaVersion": str(value.schema_version),
        "gameKey": value.game_key,
        "environmentId": value.environment_id,
        "lineageId": value.lineage_id,
        "steamRoot": value.steam_root,
        "gameRoot": value.game_root,
        "manager.adapterId": value.manager.adapter_id,
        "manager.root": value.manager.root,
        "recipe.recipeId": value.recipe.recipe_id,
        "recipe.revision": value.recipe.revision,
        "recipe.maturity": value.recipe.maturity.value,
        "recipe.identity": value.recipe.identity.value,
        "recipe.sourceSha256": value.recipe.source.source_sha256,
        "recipe.storedPath": value.recipe.source.stored_path,
        "recipe.selected": value.recipe.selected,
        "recipe.omitted": value.recipe.omitted,
        "targetEnvironment.environmentId": value.target_environment.environment_id,
        "targetEnvironment.sourceSha256": (
            value.target_environment.source.source_sha256
        ),
        "targetEnvironment.storedPath": value.target_environment.source.stored_path,
        "targetEnvironment.dimensions": tuple(
            f"{key}={item}" for key, item in value.target_environment.dimensions
        ),
        "baselineCheckpointId": value.baseline_checkpoint_id,
    }


def _bounded_sequence_pair(
    before: tuple[str, ...], after: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    first_difference = next(
        (
            index
            for index in range(max(len(before), len(after)))
            if index >= len(before)
            or index >= len(after)
            or before[index] != after[index]
        ),
        0,
    )

    def bounded(value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) <= 7:
            return value
        start = max(0, first_difference - 3)
        window = value[start : start + 7]
        return (
            f"count={len(value)}",
            f"firstDifference={first_difference}",
            *(f"[{start + index}]={item}" for index, item in enumerate(window)),
        )

    return bounded(before), bounded(after)


def configuration_changes(
    before: SkyrimEnvironmentConfiguration,
    after: SkyrimEnvironmentConfiguration,
) -> tuple[ConfigurationChange, ...]:
    before = configuration_from_dict(configuration_to_dict(before))
    after = configuration_from_dict(configuration_to_dict(after))
    before_fields = _flatten_configuration(before)
    after_fields = _flatten_configuration(after)
    changes: list[ConfigurationChange] = []
    for field in sorted(before_fields):
        before_value = before_fields[field]
        after_value = after_fields[field]
        if before_value == after_value:
            continue
        if isinstance(before_value, tuple) and isinstance(after_value, tuple):
            before_value, after_value = _bounded_sequence_pair(
                before_value, after_value
            )
        changes.append(
            ConfigurationChange(
                field=field,
                before=before_value,
                after=after_value,
            )
        )
    return tuple(changes)
