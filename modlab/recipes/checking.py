"""Evidence-based compatibility checks with no repair side effects."""

from .model import (
    CheckState,
    CompatibilityFinding,
    EnvironmentEvidence,
    RecipeComponent,
)


def check_component(
    component: RecipeComponent,
    environment: EnvironmentEvidence,
) -> tuple[CompatibilityFinding, ...]:
    findings: list[CompatibilityFinding] = []
    exact_artifact_pinned = bool(
        component.archive_sha256 or component.installed_tree_sha256
    )
    for constraint in component.constraints:
        actual = environment.dimensions.get(constraint.dimension)
        reason = constraint.reason
        if actual is None:
            state = CheckState.UNKNOWN
        elif actual not in constraint.allowed_values:
            state = CheckState.BLOCKED
        elif not exact_artifact_pinned:
            state = CheckState.UNKNOWN
            reason = (
                f"{constraint.reason} The target matches, but no exact "
                "component artifact hash is pinned."
            )
        else:
            state = CheckState.PASSED
        findings.append(
            CompatibilityFinding(
                state=state,
                component_id=component.component_id,
                dimension=constraint.dimension,
                allowed_values=constraint.allowed_values,
                actual_value=actual,
                reason=reason,
            )
        )
    return tuple(findings)
