"""Public API for Foundation Recipe handling."""

from .loading import (
    RecipeFormatError,
    load_environment,
    load_recipe,
    parse_environment,
    parse_recipe,
)
from .model import (
    CheckState,
    CompatibilityConstraint,
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
]
