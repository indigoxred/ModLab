"""Read-only selection and dependency planning for Foundation Recipes."""

from .checking import check_component
from .model import (
    CheckState,
    ComponentImportance,
    EnvironmentEvidence,
    FoundationRecipe,
    RecipeIdentity,
    RecipeReview,
)


def review_recipe(
    recipe: FoundationRecipe,
    environment: EnvironmentEvidence,
    select: tuple[str, ...] = (),
    omit: tuple[str, ...] = (),
) -> RecipeReview:
    components = recipe.component_map()
    requested = set(select) | set(omit)
    unknown = sorted(requested - set(components))
    if unknown:
        raise ValueError(f"Unknown component IDs: {', '.join(unknown)}")

    selected = {
        item.component_id for item in recipe.components if item.default_selected
    }
    selected.update(select)
    selected.difference_update(omit)

    dependency_proposals = tuple(
        sorted(
            {
                dependency
                for component_id in selected
                for dependency in components[component_id].requires
                if dependency not in selected
            }
        )
    )
    incompatibilities = tuple(
        sorted(
            {
                tuple(sorted((component_id, incompatible)))
                for component_id in selected
                for incompatible in components[component_id].incompatible_with
                if incompatible in selected
            }
        )
    )
    required_missing = any(
        item.importance is ComponentImportance.REQUIRED
        and item.component_id not in selected
        for item in recipe.components
    )
    defaults = {
        item.component_id for item in recipe.components if item.default_selected
    }
    if required_missing or dependency_proposals or incompatibilities:
        identity = RecipeIdentity.INCOMPLETE
    elif selected != defaults:
        identity = RecipeIdentity.CUSTOM
    else:
        identity = RecipeIdentity.ORIGINAL

    findings = tuple(
        finding
        for component_id in sorted(selected)
        for finding in check_component(components[component_id], environment)
    )
    blocked = any(item.state is CheckState.BLOCKED for item in findings)
    return RecipeReview(
        recipe_id=recipe.recipe_id,
        revision=recipe.revision,
        maturity=recipe.maturity,
        identity=identity,
        selected=tuple(sorted(selected)),
        omitted=tuple(sorted(set(components) - selected)),
        dependency_proposals=dependency_proposals,
        incompatibilities=incompatibilities,
        findings=findings,
        ready_for_approval=(identity is not RecipeIdentity.INCOMPLETE and not blocked),
    )
