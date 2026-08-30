"""Immutable values shared by recipe loaders, checks, and adapters."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class RecipeMaturity(StrEnum):
    DRAFT = "Draft"
    CANDIDATE = "Candidate"
    VERIFIED = "Verified"
    RETIRED = "Retired"
    BROKEN = "Broken"


class ComponentImportance(StrEnum):
    REQUIRED = "Required"
    RECOMMENDED_DEFAULT = "Recommended Default"
    CONDITIONAL = "Conditional"
    OPINIONATED = "Opinionated"
    OPTIONAL = "Optional"


class CheckState(StrEnum):
    PASSED = "Passed"
    WARNING = "Warning"
    BLOCKED = "Blocked"
    UNKNOWN = "Unknown"


class RecipeIdentity(StrEnum):
    ORIGINAL = "Original"
    CUSTOM = "Custom"
    INCOMPLETE = "Incomplete"


@dataclass(frozen=True)
class CompatibilityConstraint:
    dimension: str
    allowed_values: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class RecipeTarget:
    game: str
    edition: str
    distribution: str
    engine_lane: str
    adapter: str


@dataclass(frozen=True)
class RecipeComponent:
    component_id: str
    display_name: str
    importance: ComponentImportance
    default_selected: bool
    role: str
    deployment: str
    requires: tuple[str, ...]
    incompatible_with: tuple[str, ...]
    constraints: tuple[CompatibilityConstraint, ...]
    rationale: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class FoundationRecipe:
    schema_version: int
    recipe_id: str
    revision: str
    display_name: str
    maturity: RecipeMaturity
    target: RecipeTarget
    researched_at: str
    components: tuple[RecipeComponent, ...]

    def component_map(self) -> Mapping[str, RecipeComponent]:
        return MappingProxyType({item.component_id: item for item in self.components})


@dataclass(frozen=True)
class EnvironmentEvidence:
    schema_version: int
    environment_id: str
    dimensions: Mapping[str, str]


@dataclass(frozen=True)
class CompatibilityFinding:
    state: CheckState
    component_id: str
    dimension: str
    allowed_values: tuple[str, ...]
    actual_value: str | None
    reason: str
