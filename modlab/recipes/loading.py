"""Strict JSON loading for untrusted recipe and environment files."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .model import (
    CompatibilityConstraint,
    ComponentImportance,
    EnvironmentEvidence,
    FoundationRecipe,
    RecipeComponent,
    RecipeMaturity,
    RecipeTarget,
)


class RecipeFormatError(ValueError):
    """A recipe or environment file cannot be represented safely."""

    def __init__(self, *issues: str):
        if not issues:
            raise ValueError("RecipeFormatError requires at least one issue")
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class LoadedRecipeSource:
    path: Path
    data: bytes
    sha256: str
    recipe: FoundationRecipe


@dataclass(frozen=True)
class LoadedEnvironmentSource:
    path: Path
    data: bytes
    sha256: str
    environment: EnvironmentEvidence


_RECIPE_FIELDS = {
    "schemaVersion",
    "recipeId",
    "revision",
    "displayName",
    "maturity",
    "target",
    "researchedAt",
    "components",
}
_TARGET_FIELDS = {"game", "edition", "distribution", "engineLane", "adapter"}
_COMPONENT_REQUIRED_FIELDS = {
    "componentId",
    "displayName",
    "importance",
    "defaultSelected",
    "role",
    "deployment",
    "requires",
    "incompatibleWith",
    "constraints",
    "rationale",
    "sources",
}
_COMPONENT_OPTIONAL_FIELDS = {"version", "archiveSha256", "installedTreeSha256"}
_CONSTRAINT_FIELDS = {"dimension", "allowedValues", "reason"}
_ENVIRONMENT_FIELDS = {"schemaVersion", "environmentId", "dimensions"}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecipeFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _exact_fields(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise RecipeFormatError(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RecipeFormatError(f"{path} must be an object")
    return value


def _schema_version(data: Mapping[str, Any], path: str) -> int:
    value = data.get("schemaVersion")
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise RecipeFormatError(f"{path}.schemaVersion must be integer 1")
    return value


def _text(data: Mapping[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecipeFormatError(f"{path}.{key} must be a non-empty string")
    return value.strip()


def _boolean(data: Mapping[str, Any], key: str, path: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise RecipeFormatError(f"{path}.{key} must be a boolean")
    return value


def _optional_text(data: Mapping[str, Any], key: str, path: str) -> str | None:
    if key not in data:
        return None
    return _text(data, key, path)


def _optional_sha256(data: Mapping[str, Any], key: str, path: str) -> str | None:
    value = _optional_text(data, key, path)
    if value is None:
        return None
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise RecipeFormatError(f"{path}.{key} must be a 64-character SHA-256 value")
    return normalized


def _array(data: Mapping[str, Any], key: str, path: str) -> Sequence[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise RecipeFormatError(f"{path}.{key} must be an array")
    return value


def _text_array(
    data: Mapping[str, Any],
    key: str,
    path: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    values = _array(data, key, path)
    if not allow_empty and not values:
        raise RecipeFormatError(f"{path}.{key} must contain at least one value")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise RecipeFormatError(
                f"{path}.{key}[{index}] must be a non-empty string"
            )
        result.append(value.strip())
    if len(set(result)) != len(result):
        raise RecipeFormatError(f"{path}.{key} contains duplicate values")
    return tuple(result)


def _enum(enum_type: type[Any], value: str, path: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise RecipeFormatError(f"{path} must be one of: {allowed}") from error


def _parse_constraint(value: object, path: str) -> CompatibilityConstraint:
    data = _object(value, path)
    _exact_fields(data, _CONSTRAINT_FIELDS, path)
    return CompatibilityConstraint(
        dimension=_text(data, "dimension", path),
        allowed_values=_text_array(
            data, "allowedValues", path, allow_empty=False
        ),
        reason=_text(data, "reason", path),
    )


def _parse_component(value: object, index: int) -> RecipeComponent:
    path = f"recipe.components[{index}]"
    data = _object(value, path)
    expected_fields = _COMPONENT_REQUIRED_FIELDS | (
        set(data) & _COMPONENT_OPTIONAL_FIELDS
    )
    _exact_fields(data, expected_fields, path)
    importance_text = _text(data, "importance", path)
    importance = _enum(
        ComponentImportance, importance_text, f"{path}.importance"
    )
    default_selected = _boolean(data, "defaultSelected", path)
    if importance is ComponentImportance.REQUIRED and not default_selected:
        raise RecipeFormatError(
            f"{path}: a Required component must be selected by default"
        )
    constraints = tuple(
        _parse_constraint(item, f"{path}.constraints[{constraint_index}]")
        for constraint_index, item in enumerate(_array(data, "constraints", path))
    )
    return RecipeComponent(
        component_id=_text(data, "componentId", path),
        display_name=_text(data, "displayName", path),
        importance=importance,
        default_selected=default_selected,
        role=_text(data, "role", path),
        deployment=_text(data, "deployment", path),
        requires=_text_array(data, "requires", path),
        incompatible_with=_text_array(data, "incompatibleWith", path),
        constraints=constraints,
        rationale=_text(data, "rationale", path),
        sources=_text_array(data, "sources", path),
        version=_optional_text(data, "version", path),
        archive_sha256=_optional_sha256(data, "archiveSha256", path),
        installed_tree_sha256=_optional_sha256(
            data, "installedTreeSha256", path
        ),
    )


def parse_recipe(value: object) -> FoundationRecipe:
    data = _object(value, "recipe")
    _exact_fields(data, _RECIPE_FIELDS, "recipe")
    schema_version = _schema_version(data, "recipe")
    maturity_text = _text(data, "maturity", "recipe")
    maturity = _enum(RecipeMaturity, maturity_text, "recipe.maturity")

    target_data = _object(data.get("target"), "recipe.target")
    _exact_fields(target_data, _TARGET_FIELDS, "recipe.target")
    target = RecipeTarget(
        game=_text(target_data, "game", "recipe.target"),
        edition=_text(target_data, "edition", "recipe.target"),
        distribution=_text(target_data, "distribution", "recipe.target"),
        engine_lane=_text(target_data, "engineLane", "recipe.target"),
        adapter=_text(target_data, "adapter", "recipe.target"),
    )

    component_values = _array(data, "components", "recipe")
    if not component_values:
        raise RecipeFormatError("recipe.components must contain at least one component")
    components = tuple(
        _parse_component(item, index)
        for index, item in enumerate(component_values)
    )

    identifiers = [item.component_id for item in components]
    if len(set(identifiers)) != len(identifiers):
        raise RecipeFormatError("recipe.components contains duplicate componentId values")
    known = set(identifiers)
    for component in components:
        for relationship_name, relationships in (
            ("requires", component.requires),
            ("incompatibleWith", component.incompatible_with),
        ):
            if component.component_id in relationships:
                raise RecipeFormatError(
                    f"component {component.component_id} cannot reference itself in {relationship_name}"
                )
            unknown = sorted(set(relationships) - known)
            if unknown:
                raise RecipeFormatError(
                    f"component {component.component_id} references unknown {relationship_name}: "
                    + ", ".join(unknown)
                )

    return FoundationRecipe(
        schema_version=schema_version,
        recipe_id=_text(data, "recipeId", "recipe"),
        revision=_text(data, "revision", "recipe"),
        display_name=_text(data, "displayName", "recipe"),
        maturity=maturity,
        target=target,
        researched_at=_text(data, "researchedAt", "recipe"),
        components=components,
    )


def parse_environment(value: object) -> EnvironmentEvidence:
    data = _object(value, "environment")
    _exact_fields(data, _ENVIRONMENT_FIELDS, "environment")
    schema_version = _schema_version(data, "environment")
    dimensions_data = _object(data.get("dimensions"), "environment.dimensions")
    dimensions: dict[str, str] = {}
    for raw_key, raw_value in dimensions_data.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise RecipeFormatError(
                "environment.dimensions keys must be non-empty strings"
            )
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise RecipeFormatError(
                f"environment.dimensions.{raw_key} must be a non-empty string"
            )
        dimensions[raw_key.strip()] = raw_value.strip()
    return EnvironmentEvidence(
        schema_version=schema_version,
        environment_id=_text(data, "environmentId", "environment"),
        dimensions=MappingProxyType(dimensions),
    )


def _is_redirected(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _read_source_bytes(path: Path) -> bytes:
    source_path = Path(path)
    absolute_path = source_path.absolute()
    try:
        metadata = absolute_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or any(
            _is_redirected(candidate)
            for candidate in (absolute_path, *absolute_path.parents)
        ):
            raise RecipeFormatError(
                f"{path} must be a regular non-redirected file"
            )
        return absolute_path.read_bytes()
    except RecipeFormatError:
        raise
    except OSError as error:
        raise RecipeFormatError(f"Cannot read {path}: {error}") from error


def _parse_source_json(path: Path, data: bytes) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RecipeFormatError(f"{path} is not valid UTF-8: {error}") from error
    try:
        return json.loads(text, object_pairs_hook=_without_duplicate_keys)
    except json.JSONDecodeError as error:
        raise RecipeFormatError(
            f"{path} is not valid JSON: line {error.lineno}, column {error.colno}"
        ) from error


def load_recipe_source(path: Path) -> LoadedRecipeSource:
    source_path = Path(path)
    data = _read_source_bytes(source_path)
    return LoadedRecipeSource(
        path=source_path,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        recipe=parse_recipe(_parse_source_json(source_path, data)),
    )


def load_environment_source(path: Path) -> LoadedEnvironmentSource:
    source_path = Path(path)
    data = _read_source_bytes(source_path)
    return LoadedEnvironmentSource(
        path=source_path,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        environment=parse_environment(_parse_source_json(source_path, data)),
    )


def load_recipe(path: Path) -> FoundationRecipe:
    return load_recipe_source(path).recipe


def load_environment(path: Path) -> EnvironmentEvidence:
    return load_environment_source(path).environment
