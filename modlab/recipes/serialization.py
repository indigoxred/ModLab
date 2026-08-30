"""Stable machine-readable recipe review results."""

from .model import RecipeReview


def review_to_dict(review: RecipeReview) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "recipeId": review.recipe_id,
        "revision": review.revision,
        "maturity": review.maturity.value,
        "identity": review.identity.value,
        "selected": list(review.selected),
        "omitted": list(review.omitted),
        "dependencyProposals": list(review.dependency_proposals),
        "incompatibilities": [list(pair) for pair in review.incompatibilities],
        "findings": [
            {
                "state": finding.state.value,
                "componentId": finding.component_id,
                "dimension": finding.dimension,
                "allowedValues": list(finding.allowed_values),
                "actualValue": finding.actual_value,
                "reason": finding.reason,
            }
            for finding in review.findings
        ],
        "readyForApproval": review.ready_for_approval,
        "actionsPerformed": [],
    }
