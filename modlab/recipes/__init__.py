"""Public API for Foundation Recipe handling."""

from .loading import (
    RecipeFormatError,
    load_environment,
    load_recipe,
    parse_environment,
    parse_recipe,
)
from .checking import check_component
from .model import (
    CheckState,
    CompatibilityConstraint,
    CompatibilityFinding,
    ComponentImportance,
    EnvironmentEvidence,
    FoundationRecipe,
    RecipeComponent,
    RecipeIdentity,
    RecipeMaturity,
    RecipeTarget,
)

__all__ = [
    "CheckState",
    "CompatibilityConstraint",
    "CompatibilityFinding",
    "ComponentImportance",
    "EnvironmentEvidence",
    "FoundationRecipe",
    "RecipeComponent",
    "RecipeFormatError",
    "RecipeIdentity",
    "RecipeMaturity",
    "RecipeTarget",
    "load_environment",
    "load_recipe",
    "parse_environment",
    "parse_recipe",
    "check_component",
]
