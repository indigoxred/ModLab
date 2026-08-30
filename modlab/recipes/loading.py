"""Strict JSON loading for untrusted recipe and environment files."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
    schema_version = _schema_version(data, "recipe")
    maturity_text = _text(data, "maturity", "recipe")
    maturity = _enum(RecipeMaturity, maturity_text, "recipe.maturity")

    target_data = _object(data.get("target"), "recipe.target")
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


def _load_json(path: Path) -> object:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as error:
        raise RecipeFormatError(
            f"{path} is not valid JSON: line {error.lineno}, column {error.colno}"
        ) from error
    except OSError as error:
        raise RecipeFormatError(f"Cannot read {path}: {error}") from error


def load_recipe(path: Path) -> FoundationRecipe:
    return parse_recipe(_load_json(path))


def load_environment(path: Path) -> EnvironmentEvidence:
    return parse_environment(_load_json(path))
